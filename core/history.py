"""最小 snapshot/rollback 基础(Core, 无 UI 依赖)。

- .history/index.jsonl + 快照文件
- 记录支持单文件(change 长度 1)与多文件(changes 列表, 如 confirm)
- Snapshot: 先 prepare(backups 就位, index 未写)→ 业务操作 → commit;
  业务失败 → restore(恢复业务文件) + discard(清理 backups)
  = 失败的 operation 不出现在 undo history 中
- undo-last: preflight 全量验证 → 保存 undo 前状态 → 应用 restore;
  中途失败 → 自动回滚到 undo 前状态(真正 all-or-nothing)
- 严格 schema 校验: 坏记录 → DataIntegrityError(不 silent skip)
- undo 成功后才移除 index record / 同步 Project 内存 metadata
"""
from __future__ import annotations

import datetime
import json
import shutil
from pathlib import Path
from typing import Any, Optional

from .project import Project
from .storage import DataIntegrityError, StorageError, atomic_write_text
from .locks import history_lock

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
    if rec.get("metadata") is not None and not isinstance(rec.get("metadata"), dict):
        raise DataIntegrityError("history metadata 必须是 object")
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


class Snapshot:
    """已准备但未提交的 history operation(backups 就位, index 未写)。

    prepare → 业务操作 → commit / (restore + discard)
    commit:   把 record 永久写入 index(原子写全量; 失败 → index 不变)
    discard:  放弃本次 operation, 清理 backups(不写 index)
    restore:  把业务文件恢复到快照时状态(业务失败回滚用)
    """

    def __init__(self, project: Project, seq: int, operation: str,
                 changes: list[dict[str, Any]], backup_files: list[Path],
                 metadata: Optional[dict[str, Any]] = None, lock_context=None):
        self.project = project
        self.seq = seq
        self.operation = operation
        self.changes = changes
        self.backup_files = backup_files
        self._timestamp = _now_iso()
        self.metadata = dict(metadata) if metadata is not None else None
        self._lock_context = lock_context

    def _release_lock(self) -> None:
        context, self._lock_context = self._lock_context, None
        if context is not None:
            context.__exit__(None, None, None)

    def set_metadata(self, metadata: dict[str, Any]) -> None:
        if not isinstance(metadata, dict):
            raise ValueError("metadata 必须是 dict")
        self.metadata = dict(metadata)

    def record(self) -> dict[str, Any]:
        record = {
            "seq": self.seq,
            "operation": self.operation,
            "timestamp": self._timestamp,
            "changes": self.changes,
        }
        metadata = self.metadata
        if isinstance(metadata, dict):
            record["metadata"] = metadata
        return record

    def commit(self) -> dict[str, Any]:
        """把 record 永久写入 index(原子写全量)。失败 → StorageError, index 不变。"""
        hdir = _history_dir(self.project)
        hdir.mkdir(parents=True, exist_ok=True)
        idx = hdir / INDEX_NAME
        current = idx.read_text(encoding="utf-8") if idx.exists() else ""
        atomic_write_text(idx, current + json.dumps(self.record(), ensure_ascii=False) + "\n")
        result = self.record()
        self._release_lock()
        return result

    def discard(self) -> None:
        """放弃本次 operation: 清理备份文件(不写 index)。清理失败不掩盖主错误。"""
        for b in self.backup_files:
            try:
                if b.exists():
                    b.unlink()
            except OSError:
                pass
        self._release_lock()

    def restore(self, expected_current: Optional[dict[str, Optional[bytes]]] = None) -> None:
        """把业务文件恢复到快照时状态。

        尽力恢复全部 target(单个失败不中断后续恢复); 任一失败 → StorageError。
        调用方据此决定: 恢复完整 → discard; 不完整 → 保留 backups + 诚实报错。
        """
        failures: list[str] = []
        for ch in self.changes:
            try:
                target = self.project.store.safe_path(self.project.id, ch["target"])
                if expected_current is not None and ch["target"] in expected_current:
                    expected = expected_current[ch["target"]]
                    actual = target.read_bytes() if target.is_file() else None
                    if actual != expected:
                        failures.append(f"{ch['target']}: concurrent external change preserved")
                        continue
                if ch["previous"] == "present":
                    bpath = self.project.store.safe_path(self.project.id, ch["backup"])
                    content = bpath.read_text(encoding="utf-8")
                    atomic_write_text(target, content)
                else:
                    if target.exists():
                        target.unlink()
            except Exception as e:
                failures.append(f"{ch['target']}: {e}")
        if failures:
            # Keep backups for manual recovery, but never strand the global
            # history transaction lock after an incomplete guarded restore.
            self._release_lock()
            raise StorageError("恢复未完整完成: " + "; ".join(failures))


def prepare_snapshot(project: Project, operation: str, target_rels: list[str],
                     metadata: Optional[dict[str, Any]] = None) -> Snapshot:
    """准备快照: 复制全部 backups, 不写 index, 不修改业务文件。

    复制中途失败 → 已创建的 backups 全部清理, 原文件不变, index 无记录。
    seq 在 prepare 时分配; 失败后不产生孤儿 seq(index 未写)。
    """
    lock_context = history_lock(project)
    lock_context.__enter__()
    changes: list[dict[str, Any]] = []
    backup_files: list[Path] = []
    try:
        hdir = _history_dir(project)
        hdir.mkdir(parents=True, exist_ok=True)
        seq = _next_seq(project)
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
                backup_files.append(hdir / backup)
            else:
                ch["previous"] = "absent"
                ch["backup"] = None
            changes.append(ch)
    except Exception:
        # 半成品: 清理本次已创建的 backups(不写 index, 不影响已有历史)
        for b in backup_files:
            try:
                if b.exists():
                    b.unlink()
            except OSError:
                pass
        lock_context.__exit__(None, None, None)
        raise
    return Snapshot(project, seq, operation, changes, backup_files, metadata, lock_context)


def snapshot(project: Project, operation: str, target_rel: str) -> dict[str, Any]:
    """立即快照并记录(单文件, 兼容入口)。

    业务方请用 prepare_snapshot + commit/restore/discard 做事务化调用。
    """
    return snapshot_multi(project, operation, [target_rel])


def snapshot_multi(project: Project, operation: str, target_rels: list[str]) -> dict[str, Any]:
    """立即快照并记录(多文件, 兼容入口)。"""
    s = prepare_snapshot(project, operation, target_rels)
    try:
        return s.commit()
    except Exception:
        # This compatibility helper snapshots only; no business bytes were
        # mutated between prepare and commit, so discard is always safe.
        s.discard()
        raise


def list_history(project: Project) -> list[dict[str, Any]]:
    """读取全部历史记录(新→旧)。"""
    return list(reversed(_read_records(project)))


def _preflight_undo(project: Project, changes: list[dict[str, Any]]) -> dict[str, Any]:
    """undo 前一次性验证整个 changes 列表(§preflight)。

    任何问题(路径越界 / previous 非法 / backup 缺失 / 不可读 /
    project.json backup 非合法 JSON)→ 抛错, 业务文件 0 修改。
    返回 ready: {target_rel: (content, parsed_project_json|None) 或 None(absent)}
    """
    ready: dict[str, Any] = {}
    for ch in changes:
        target_rel = ch["target"]
        project.store.safe_path(project.id, target_rel)  # 路径越界 → StorageError
        if ch["previous"] not in VALID_PREVIOUS:
            raise DataIntegrityError(f"history change previous 非法: {ch['previous']!r}(target={target_rel})")
        if ch["previous"] == "present":
            backup = ch.get("backup")
            if not backup:
                raise DataIntegrityError(f"history change present 但缺少 backup: {target_rel}")
            bpath = project.store.safe_path(project.id, backup)  # backup 路径越界 → StorageError
            if not bpath.exists():
                raise StorageError(f"快照文件缺失: {backup}, 无法回滚")
            try:
                content = bpath.read_text(encoding="utf-8")
            except OSError as e:
                raise StorageError(f"快照文件不可读: {backup}: {e}")
            if target_rel == "project.json":
                try:
                    parsed = json.loads(content)
                except json.JSONDecodeError as e:
                    raise DataIntegrityError(f"回滚 project.json 将写入损坏 JSON: {e.msg}")
                if not isinstance(parsed, dict):
                    raise DataIntegrityError("回滚后的 project.json 根节点不是对象")
                ready[target_rel] = (content, parsed)
            else:
                ready[target_rel] = (content, None)
        else:
            ready[target_rel] = None
    return ready


def _capture_current_state(project: Project, changes: list[dict[str, Any]]) -> dict[str, Any]:
    """记录 undo 开始前, 本次 changes 涉及文件的当前状态(供失败自动回滚)。"""
    captured: dict[str, Any] = {}
    for ch in changes:
        target = project.store.safe_path(project.id, ch["target"])
        if target.exists():
            try:
                captured[ch["target"]] = ("present", target.read_text(encoding="utf-8"))
            except OSError as e:
                raise StorageError(f"无法保存回滚前状态: {ch['target']}: {e}")
        else:
            captured[ch["target"]] = ("absent", None)
    return captured


def _apply_undo(project: Project, changes: list[dict[str, Any]], ready: dict[str, Any]) -> None:
    """按 record 恢复目标文件(present → 写回快照内容; absent → 删除; 幂等)。"""
    for ch in changes:
        target = project.store.safe_path(project.id, ch["target"])
        if ch["previous"] == "present":
            content, _ = ready[ch["target"]]
            atomic_write_text(target, content)
        else:
            if target.exists():
                target.unlink()


def _apply_captured(project: Project, captured: dict[str, Any]) -> None:
    """把目标文件恢复到 undo 开始前状态(尽力恢复全部; 任一失败 → StorageError)。"""
    failures: list[str] = []
    for rel, (state, content) in captured.items():
        try:
            target = project.store.safe_path(project.id, rel)
            if state == "present":
                atomic_write_text(target, content)
            else:
                if target.exists():
                    target.unlink()
        except Exception as e:
            failures.append(f"{rel}: {e}")
    if failures:
        raise StorageError("自动恢复未完整完成: " + "; ".join(failures))


def _remove_record(project: Project, rec: dict[str, Any]) -> None:
    """从 index 移除指定记录(deep equality 匹配, 防重复 seq 误删)。"""
    idx = _history_dir(project) / INDEX_NAME
    if not idx.exists():
        return
    try:
        lines = idx.read_text(encoding="utf-8").splitlines()
    except OSError as e:
        raise DataIntegrityError(f"无法读取 history index: {e}")
    remaining: list[str] = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            raise DataIntegrityError("history index 损坏, 无法移除回滚记录")
        if r != rec:
            remaining.append(line)
    atomic_write_text(idx, "\n".join(remaining) + ("\n" if remaining else ""))


def _undo_last_locked(project: Project) -> dict[str, Any]:
    """回滚最近一次快照 — 真正 all-or-nothing。

    1. preflight: 一次性验证整个 changes(任何问题 → 失败, 0 修改)
    2. 保存 undo 前状态(仅本次涉及文件)
    3. 应用 restore; 中途失败 → 自动回滚到 undo 前状态
    4. 全部成功后才: 同步 Project.metadata(project.json 被恢复时) + 移除 index record
    无记录 → StorageError。
    """
    records = _read_records(project)
    if not records:
        raise StorageError("没有可回滚的历史记录")

    rec = records[-1]
    changes = rec["changes"]

    # 1) Preflight: 修改任何文件之前一次性验证(§2.1)
    ready = _preflight_undo(project, changes)

    # 2) 保存 undo 开始前状态(§2.2)
    captured = _capture_current_state(project, changes)

    # 3) 应用 restore; 失败 → 自动回滚到 undo 前(§2.2)
    try:
        _apply_undo(project, changes, ready)
    except Exception as e:
        try:
            _apply_captured(project, captured)
        except Exception as rb:
            raise DataIntegrityError(
                "回滚失败且自动恢复未完整完成, 请运行 novel validate") from e
        raise StorageError(f"回滚失败, 已恢复到回滚前状态") from e

    # 4) 整个 undo 成功后才: 同步内存 metadata(§2.4) + 移除 index record(§2.3)
    for ch in changes:
        if ch["target"] == "project.json":
            if ch["previous"] == "present":
                _, parsed = ready["project.json"]
                project.metadata = parsed
            else:
                raise DataIntegrityError("回滚删除了 project.json, 项目已损坏")
            break
    _remove_record(project, rec)
    return rec


def undo_last(project: Project) -> dict[str, Any]:
    """Serialize undo against every prepared/committing history transaction."""
    with history_lock(project):
        return _undo_last_locked(project)
