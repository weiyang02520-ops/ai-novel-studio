"""最小 snapshot/rollback 基础(Core, 无 UI 依赖)。

- .history/index.jsonl + 快照文件
- 记录支持单文件(change 长度 1)与多文件(changes 列表, 如 confirm)
- undo-last: 对整个 operation 成组恢复(无 partial rollback)
- 严格 schema 校验: 坏记录 → DataIntegrityError(不 silent skip)
- undo 恢复 project.json 后同步 Project 内存 metadata
"""
from __future__ import annotations

import datetime
import json
import shutil
from pathlib import Path
from typing import Any, Optional

from .project import Project
from .storage import DataIntegrityError, StorageError, atomic_write_text

INDEX_NAME = "index.jsonl"

VALID_PREVIOUS = ("present", "absent")


def _now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _history_dir(project: Project) -> Path:
    return project.store.safe_path(project.id, ".history")


def _validate_record(rec: dict[str, Any], line: str) -> None:
    """history 记录最小 schema 校验(非法 → DataIntegrityError, 不默默跳过)。"""
    seq = rec.get("seq")
    if not isinstance(seq, int) or isinstance(seq, bool) or seq <= 0:
        raise DataIntegrityError(f"history 记录 seq 非法: {seq!r}(行: {line[:80]})")
    op = rec.get("operation")
    if not isinstance(op, str) or not op:
        raise DataIntegrityError(f"history 记录 operation 非法: {op!r}")
    ts = rec.get("timestamp")
    if not isinstance(ts, str) or not ts:
        raise DataIntegrityError(f"history 记录 timestamp 非法: {ts!r}")

    changes = rec.get("changes")
    if not isinstance(changes, list) or not changes:
        raise DataIntegrityError(f"history 记录 changes 非法: {changes!r}")
    for ch in changes:
        if not isinstance(ch, dict):
            raise DataIntegrityError(f"history change 非法: {ch!r}")
        target = ch.get("target")
        if not isinstance(target, str) or not target:
            raise DataIntegrityError(f"history change target 非法: {target!r}")
        prev = ch.get("previous")
        if prev not in VALID_PREVIOUS:
            raise DataIntegrityError(f"history change previous 非法: {prev!r}")
        backup = ch.get("backup")
        if backup is not None and not isinstance(backup, str):
            raise DataIntegrityError(f"history change backup 非法: {backup!r}")
        if prev == "present" and not backup:
            raise DataIntegrityError(f"history change present 但缺少 backup: {target!r}")


def _read_records(project: Project) -> list[dict[str, Any]]:
    """读取全部历史记录(旧→新); 任何非空坏行 → DataIntegrityError。"""
    idx = _history_dir(project) / INDEX_NAME
    records = []
    if not idx.exists():
        return records
    try:
        lines = idx.read_text(encoding="utf-8").splitlines()
    except OSError as e:
        raise DataIntegrityError(f"无法读取 history index: {e}")
    for line in lines:
        line = line.strip()
        if not line:
            continue  # 空行允许
        try:
            rec = json.loads(line)
        except json.JSONDecodeError as e:
            raise DataIntegrityError(
                f"history index 第 {len(records) + 1} 行 JSON 损坏: {e.msg}")
        if not isinstance(rec, dict):
            raise DataIntegrityError("history 记录不是对象")
        _validate_record(rec, line)
        records.append(rec)
    return records


def _next_seq(project: Project) -> int:
    """下一个 seq。index 损坏时拒绝继续写(避免重复 seq/混乱)。"""
    records = _read_records(project)
    return (records[-1]["seq"] + 1) if records else 1


def _backup_name(seq: int, target_rel: str) -> str:
    safe = target_rel.replace("/", "_").replace("\\", "_")
    return f"{seq:05d}_{safe}.bak"


def snapshot(project: Project, operation: str, target_rel: str) -> dict[str, Any]:
    """单文件修改前快照(UPDATE: 复制旧内容; absent: 记录 previous=absent)。"""
    return snapshot_multi(project, operation, [target_rel])


def snapshot_multi(project: Project, operation: str, target_rels: list[str]) -> dict[str, Any]:
    """多文件修改前快照(如 confirm): 对每个目标分别处理。

    返回记录 {seq, operation, timestamp, changes: [{target, previous, backup}]}。
    """
    hdir = _history_dir(project)
    hdir.mkdir(parents=True, exist_ok=True)
    seq = _next_seq(project)

    changes = []
    for target_rel in target_rels:
        target = project.store.safe_path(project.id, target_rel)
        ch: dict[str, Any] = {"target": target_rel}
        if target.exists():
            backup = _backup_name(seq, target_rel)
            try:
                shutil.copy2(target, hdir / backup)
            except OSError as e:
                raise StorageError(f"快照失败 {target_rel}: {e}")
            ch["previous"] = "present"
            ch["backup"] = f".history/{backup}"
        else:
            ch["previous"] = "absent"
            ch["backup"] = None
        changes.append(ch)

    rec = {
        "seq": seq,
        "operation": operation,
        "timestamp": _now_iso(),
        "changes": changes,
    }
    idx = hdir / INDEX_NAME
    with idx.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return rec


def list_history(project: Project) -> list[dict[str, Any]]:
    """读取全部历史记录(新→旧)。"""
    return list(reversed(_read_records(project)))


def undo_last(project: Project) -> dict[str, Any]:
    """回滚最近一次快照(整个 operation 成组恢复, 无 partial)。

    每个 change:
      - previous=present: 用快照内容恢复目标
      - previous=absent:  删除目标(若存在)
    若目标含 project.json: 恢复后同步 Project.metadata(内存对象)。
    无记录 → StorageError。
    """
    records = _read_records(project)
    if not records:
        raise StorageError("没有可回滚的历史记录")

    rec = records[-1]
    hdir = _history_dir(project)

    # 成组恢复: 先全部准备, 再逐个执行(任一失败 → 明确报错, 不半途)
    restored_meta: Optional[dict[str, Any]] = None
    for ch in rec["changes"]:
        target = project.store.safe_path(project.id, ch["target"])
        if ch["previous"] == "present":
            backup = project.store.safe_path(project.id, ch["backup"])
            if not backup.exists():
                raise StorageError(f"快照文件缺失: {ch['backup']}, 无法回滚")
            content = backup.read_text(encoding="utf-8")
            atomic_write_text(target, content)
            if ch["target"] == "project.json":
                try:
                    restored_meta = json.loads(content)
                except json.JSONDecodeError as e:
                    raise DataIntegrityError(f"回滚后的 project.json 损坏: {e.msg}")
        else:
            # previous=absent: 删除目标
            if target.exists():
                target.unlink()

    # 同步内存 metadata(project.json 被恢复时)
    if restored_meta is not None:
        if not isinstance(restored_meta, dict):
            raise DataIntegrityError("回滚后的 project.json 根节点不是对象")
        project.metadata = restored_meta

    # 从 index 移除该条
    idx = hdir / INDEX_NAME
    remaining = []
    if idx.exists():
        for line in idx.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                raise DataIntegrityError("history index 损坏, 无法移除回滚记录")
            if r.get("seq") != rec["seq"]:
                remaining.append(line)
    atomic_write_text(idx, "\n".join(remaining) + ("\n" if remaining else ""))

    return rec
