"""最小 snapshot/rollback 基础(Core, 无 UI 依赖)。

- .history/index.jsonl + 快照文件
- 对"已有内容修改"之前保留旧版本(update / confirm 的 project.json 更新)
- undo-last: 顺序回滚最近一次快照
- 每项目独立隔离
"""
from __future__ import annotations

import datetime
import json
import shutil
from pathlib import Path
from typing import Any, Optional

from .project import Project
from .storage import StorageError, atomic_write_json

INDEX_NAME = "index.jsonl"


def _now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _history_dir(project: Project) -> Path:
    return project.store.safe_path(project.id, ".history")


def _next_seq(project: Project) -> int:
    """从 index 末尾取下一个 seq(单调递增)。"""
    idx = _history_dir(project) / INDEX_NAME
    last_seq = 0
    if idx.exists():
        for line in idx.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
                if isinstance(rec.get("seq"), int):
                    last_seq = max(last_seq, rec["seq"])
            except json.JSONDecodeError:
                continue
    return last_seq + 1


def _backup_name(seq: int, target_rel: str) -> str:
    safe = target_rel.replace("/", "_").replace("\\", "_")
    return f"{seq:05d}_{safe}.bak"


def snapshot(project: Project, operation: str, target_rel: str) -> Optional[dict[str, Any]]:
    """修改前快照: 记录旧内容到 .history/, 追加 index 记录。

    - 目标不存在(CREATE 场景): previous=absent, 不复制文件
    - 目标存在(UPDATE 场景): 复制旧内容, backup 指向快照文件
    返回快照记录(供 undo-last)。
    """
    hdir = _history_dir(project)
    hdir.mkdir(parents=True, exist_ok=True)
    target = project.store.safe_path(project.id, target_rel)

    seq = _next_seq(project)
    rec: dict[str, Any] = {
        "seq": seq,
        "operation": operation,
        "target": target_rel,
        "backup": None,
        "timestamp": _now_iso(),
    }
    if target.exists():
        backup = _backup_name(seq, target_rel)
        try:
            shutil.copy2(target, hdir / backup)
        except OSError as e:
            raise StorageError(f"快照失败 {target_rel}: {e}")
        rec["backup"] = f".history/{backup}"
        rec["previous"] = "present"
    else:
        rec["previous"] = "absent"

    idx = hdir / INDEX_NAME
    with idx.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return rec


def list_history(project: Project) -> list[dict[str, Any]]:
    """读取全部历史记录(新→旧)。"""
    idx = _history_dir(project) / INDEX_NAME
    records = []
    if idx.exists():
        for line in idx.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return list(reversed(records))


def undo_last(project: Project) -> dict[str, Any]:
    """回滚最近一次快照。

    - UPDATE: 用快照内容覆盖目标
    - absent(CREATE): 删除目标(若存在)
    回滚后从 index 移除该条(保持"最后一条 = 可回滚"语义)。
    无记录 → StorageError。
    """
    records = list_history(project)  # 新→旧
    if not records:
        raise StorageError("没有可回滚的历史记录")

    rec = records[0]  # 最近一条
    target = project.store.safe_path(project.id, rec["target"])

    if rec.get("previous") == "present" and rec.get("backup"):
        backup = project.store.safe_path(project.id, rec["backup"])
        if not backup.exists():
            raise StorageError(f"快照文件缺失: {rec['backup']}, 无法回滚")
        # 原子写回: 读快照内容 → 写目标
        content = backup.read_text(encoding="utf-8")
        from .storage import atomic_write_text
        atomic_write_text(target, content)
    else:
        # previous=absent(CREATE): 删除目标
        if target.exists():
            target.unlink()

    # 从 index 移除该条
    idx = _history_dir(project) / INDEX_NAME
    remaining = []
    if idx.exists():
        for line in idx.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if r.get("seq") != rec["seq"]:
                remaining.append(line)
    from .storage import atomic_write_text
    atomic_write_text(idx, "\n".join(remaining) + ("\n" if remaining else ""))

    return rec
