"""Safe optimistic project mutations built on the existing snapshot history."""
from __future__ import annotations

import dataclasses
import difflib
import hashlib
from typing import Any, Callable, Optional

from .history import prepare_snapshot
from .project import Project
from .storage import atomic_write_text

ABSENT = "ABSENT"


class MutationError(Exception):
    """Stable, user/model-facing mutation failure."""

    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(f"{code}: {message}")


@dataclasses.dataclass(frozen=True)
class MutationRequest:
    operation: str
    target_rel: str
    new_text: str
    expected_revision: str
    content_kind: str
    agent_id: str


@dataclasses.dataclass(frozen=True)
class DiffResult:
    added_lines: int
    removed_lines: int
    changed: bool
    preview: str


@dataclasses.dataclass(frozen=True)
class MutationResult:
    changed: bool
    created: bool
    target: str
    old_revision: str
    new_revision: str
    history_seq: Optional[int]
    operation: str
    bytes_before: int
    bytes_after: int
    diff: DiffResult


LIMITS = {
    "outline": 500_000,
    "world": 500_000,
    "character": 300_000,
    # Memory documents may grow through many entries; the per-entry 50k guard
    # is enforced by save_memory_entry before it builds the append document.
    "memory": 5_000_000,
}
FACT_KINDS = {"outline", "world", "character"}


def revision_sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def file_revision(path) -> str:
    return revision_sha256(path.read_bytes()) if path.is_file() else ABSENT


def calculate_diff(old: str, new: str, max_lines: int = 60) -> DiffResult:
    lines = list(difflib.unified_diff(
        old.splitlines(), new.splitlines(), fromfile="before", tofile="after", lineterm=""
    ))
    added = sum(1 for line in lines if line.startswith("+") and not line.startswith("+++"))
    removed = sum(1 for line in lines if line.startswith("-") and not line.startswith("---"))
    shown = lines[:max_lines]
    if len(lines) > max_lines:
        shown.append(f"... [DIFF TRUNCATED total_lines={len(lines)}]")
    return DiffResult(added, removed, old != new, "\n".join(shown))


class MutationService:
    """Owns validation, revision check, snapshot, atomic write and rollback."""

    def __init__(self, project: Project, *, snapshot_factory: Callable[..., Any] = prepare_snapshot,
                 writer: Callable[..., None] = atomic_write_text):
        self.project = project
        self.snapshot_factory = snapshot_factory
        self.writer = writer

    def _validate(self, req: MutationRequest) -> tuple[Any, bytes, bytes, str, DiffResult]:
        if not req.operation or not req.agent_id:
            raise MutationError("INVALID_MUTATION", "operation 和 agent_id 必填")
        if req.content_kind not in LIMITS:
            raise MutationError("INVALID_CONTENT_KIND", f"不支持 {req.content_kind}")
        if "\x00" in req.new_text:
            raise MutationError("NUL_REJECTED", "内容包含 NUL")
        if len(req.new_text) > LIMITS[req.content_kind]:
            raise MutationError("CONTENT_TOO_LARGE", f"{req.content_kind} 超过字符上限")
        if req.content_kind in FACT_KINDS and not req.new_text.strip():
            raise MutationError("EMPTY_DESTRUCTIVE_WRITE", "事实源不允许全空白覆盖")
        try:
            target = self.project.store.safe_path(self.project.id, req.target_rel)
        except Exception as e:
            raise MutationError("INVALID_TARGET", "目标路径不安全") from e
        old_bytes = target.read_bytes() if target.is_file() else b""
        old_rev = revision_sha256(old_bytes) if target.is_file() else ABSENT
        if req.expected_revision != old_rev:
            raise MutationError("STALE_REVISION", f"expected={req.expected_revision}, current={old_rev}")
        new_bytes = req.new_text.encode("utf-8")
        old_text = old_bytes.decode("utf-8") if old_bytes else ""
        return target, old_bytes, new_bytes, old_rev, calculate_diff(old_text, req.new_text)

    def preview(self, req: MutationRequest) -> MutationResult:
        _, old, new, old_rev, diff = self._validate(req)
        new_rev = revision_sha256(new)
        return MutationResult(old != new, old_rev == ABSENT, req.target_rel, old_rev, new_rev,
                              None, req.operation, len(old), len(new), diff)

    def mutate(self, req: MutationRequest) -> MutationResult:
        target, old, new, old_rev, diff = self._validate(req)
        new_rev = revision_sha256(new)
        if old == new:
            raise MutationError("NO_CHANGE", "新旧字节完全相同")
        try:
            snap = self.snapshot_factory(self.project, req.operation, [req.target_rel])
        except Exception as e:
            raise MutationError("SNAPSHOT_FAILED", "无法准备快照，文件未修改") from e
        # Metadata is concise and never stores full document text.
        snap._metadata = {  # type: ignore[attr-defined]
            "agent_id": req.agent_id,
            "content_kind": req.content_kind,
            "old_revision": old_rev,
            "new_revision": new_rev,
            "diff": dataclasses.asdict(diff) | {"preview": diff.preview[:4000]},
        }
        try:
            self.writer(target, req.new_text)
            if target.read_bytes() != new:
                raise MutationError("POST_WRITE_VERIFY_FAILED", "写后字节校验失败")
            snap.commit()
        except Exception as original:
            try:
                snap.restore()
                restored = target.read_bytes() if target.is_file() else b""
                if restored != old:
                    raise RuntimeError("rollback byte verification failed")
                snap.discard()
            except Exception as rollback:
                raise MutationError("ROLLBACK_FAILED", "事务失败且回滚未完整完成；保留快照供恢复") from rollback
            code = original.code if isinstance(original, MutationError) else "MUTATION_FAILED"
            raise MutationError(code, "事务失败，已恢复原字节") from original
        return MutationResult(True, old_rev == ABSENT, req.target_rel, old_rev, new_rev,
                              snap.seq, req.operation, len(old), len(new), diff)
