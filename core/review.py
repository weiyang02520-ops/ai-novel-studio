"""Revision-bound review artifacts and draft state transitions.

The long Provider call deliberately happens outside this module.  ``begin``
captures revisions while the canonical draft remains ``status=draft``;
``finalize`` performs a short-lock compare-and-swap transaction.
"""
from __future__ import annotations

import dataclasses
import datetime
import hashlib
import json
import threading
import uuid
from pathlib import Path
from typing import Any, Callable

from .chapter import (build_frontmatter, confirmed_path, draft_path,
                      parse_frontmatter)
from .history import prepare_snapshot
from .locks import chapter_lock
from .mutation import ABSENT, file_revision, revision_sha256
from .project import Project
from .storage import atomic_write_text

REPORT_LIMIT_BYTES = 500_000
_active_guard = threading.Lock()
_active: dict[tuple[str, int], str] = {}


class ReviewError(Exception):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(f"{code}: {message}")


@dataclasses.dataclass(frozen=True)
class ReviewRun:
    token: str
    chapter: int
    draft_revision: str
    report_revision: str
    reviewer_model: str
    context_hash: str
    started_at: str


@dataclasses.dataclass(frozen=True)
class ReviewResult:
    chapter: int
    verdict: str
    draft_revision: str
    report_revision: str
    report_hash: str
    report_path: str
    history_seq: int


@dataclasses.dataclass(frozen=True)
class ReopenResult:
    chapter: int
    draft_revision: str
    history_seq: int


@dataclasses.dataclass(frozen=True)
class ReviewInspection:
    chapter: int
    artifact: dict[str, Any]
    draft_revision: str
    report_revision: str
    current: bool


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def report_rel(chapter: int) -> str:
    return f"review/ch{chapter:04d}.review.json"


def report_path(project: Project, chapter: int) -> Path:
    return project.store.safe_path(project.id, report_rel(chapter))


def _plain(value: Any) -> Any:
    if hasattr(value, "to_dict"):
        return _plain(value.to_dict())
    if dataclasses.is_dataclass(value):
        return _plain(dataclasses.asdict(value))
    if isinstance(value, dict):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    return value


def _canonical(data: dict[str, Any]) -> str:
    return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


_REPORT_FIELDS = {"chapter", "verdict", "summary", "issues", "strengths",
                  "task_fulfillment", "continuity_assessment", "style_assessment",
                  "logic_assessment", "confidence", "source"}


def _report_payload_hash(data: dict[str, Any]) -> str:
    """Hash the canonical model-independent ReviewReport (no self-reference)."""
    payload = {key: data[key] for key in _REPORT_FIELDS if key in data}
    return hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()


def load_review_artifact(project: Project, chapter: int) -> dict[str, Any]:
    path = report_path(project, chapter)
    _reject_symlink(path, project.dir)
    if not path.is_file():
        raise ReviewError("REVIEW_NOT_FOUND", f"review report missing for chapter {chapter}")
    try:
        raw = path.read_bytes()
        if len(raw) > REPORT_LIMIT_BYTES:
            raise ReviewError("REVIEW_REPORT_TOO_LARGE", "report exceeds size limit")
        data = json.loads(raw.decode("utf-8"))
    except ReviewError:
        raise
    except Exception as exc:
        raise ReviewError("MALFORMED_REVIEW_REPORT", "report is not valid UTF-8 JSON") from exc
    if not isinstance(data, dict):
        raise ReviewError("MALFORMED_REVIEW_REPORT", "report root must be object")
    required = {"chapter", "draft_revision", "reviewer_model", "context_hash",
                "reviewed_at", "report_hash", "verdict", "issues"}
    if required - set(data):
        raise ReviewError("MALFORMED_REVIEW_REPORT", "report fields missing")
    if data["chapter"] != chapter or data["verdict"] not in {"PASS", "NEEDS_WORK"}:
        raise ReviewError("MALFORMED_REVIEW_REPORT", "report identity/verdict invalid")
    if not _REPORT_FIELDS.issubset(data) or not isinstance(data["issues"], list):
        raise ReviewError("MALFORMED_REVIEW_REPORT", "report payload invalid")
    if data["report_hash"] != _report_payload_hash(data):
        raise ReviewError("MALFORMED_REVIEW_REPORT", "report issues/hash invalid")
    return data


def require_current_pass_report(project: Project, chapter: int, draft_revision: str) -> dict[str, Any]:
    artifact = load_review_artifact(project, chapter)
    if artifact["draft_revision"] != draft_revision:
        raise ReviewError("STALE_REVIEW_REPORT", "report does not match current draft revision")
    if artifact["verdict"] != "PASS":
        raise ReviewError("REVIEW_NOT_PASS", "current report verdict is not PASS")
    for issue in artifact["issues"]:
        if isinstance(issue, dict) and issue.get("severity") in {"BLOCKER", "MAJOR"}:
            raise ReviewError("REVIEW_NOT_PASS", "PASS report contains major issues")
    return artifact


def _reject_symlink(path: Path, project_dir: Path) -> None:
    current = path
    while current != project_dir and current.is_relative_to(project_dir):
        if current.is_symlink():
            raise ReviewError("UNSAFE_REVIEW_PATH", "review path contains symlink")
        current = current.parent


class ReviewService:
    def __init__(self, project: Project, *, snapshot_factory: Callable[..., Any] = prepare_snapshot,
                 writer: Callable[..., None] = atomic_write_text):
        self.project = project
        self.snapshot_factory = snapshot_factory
        self.writer = writer

    def _key(self, chapter: int) -> tuple[str, int]:
        return (str(self.project.dir.resolve()), chapter)

    def begin(self, *, chapter: int, reviewer_model: str, context_hash: str) -> ReviewRun:
        with chapter_lock(self.project, chapter):
            path = draft_path(self.project, chapter)
            if confirmed_path(self.project, chapter).exists() or not path.is_file():
                raise ReviewError("REVIEW_DRAFT_NOT_FOUND", "reviewable draft missing")
            meta, body = parse_frontmatter(path.read_text(encoding="utf-8"))
            if meta.get("origin") != "ai":
                raise ReviewError("MANUAL_DRAFT_PROTECTED", "only AI drafts may be reviewed")
            if meta.get("status") != "draft":
                raise ReviewError("INVALID_REVIEW_STATUS", "review requires status=draft")
            if meta.get("generation_state") not in {"complete", "truncated"} or not body.strip():
                raise ReviewError("INVALID_REVIEW_DRAFT", "draft generation/body is invalid")
            rp = report_path(self.project, chapter)
            _reject_symlink(rp, self.project.dir)
            run = ReviewRun(uuid.uuid4().hex, chapter, file_revision(path), file_revision(rp),
                            reviewer_model, context_hash, _now())
            with _active_guard:
                key = self._key(chapter)
                if key in _active:
                    raise ReviewError("REVIEW_ALREADY_RUNNING", "a review is already active")
                _active[key] = run.token
            return run

    def abort(self, run: ReviewRun) -> None:
        with _active_guard:
            if _active.get(self._key(run.chapter)) == run.token:
                _active.pop(self._key(run.chapter), None)

    def finalize(self, run: ReviewRun, report: Any) -> ReviewResult:
        try:
            with _active_guard:
                if _active.get(self._key(run.chapter)) != run.token:
                    raise ReviewError("REVIEW_RUN_INACTIVE", "review run is not active")
            with chapter_lock(self.project, run.chapter):
                return self._finalize_locked(run, report)
        finally:
            self.abort(run)

    def _finalize_locked(self, run: ReviewRun, report: Any) -> ReviewResult:
        path, rp = draft_path(self.project, run.chapter), report_path(self.project, run.chapter)
        _reject_symlink(rp, self.project.dir)
        if file_revision(path) != run.draft_revision:
            raise ReviewError("STALE_REVIEW_DRAFT", "draft changed during review")
        if file_revision(rp) != run.report_revision:
            raise ReviewError("STALE_REVIEW_REPORT", "report changed during review")
        old = path.read_bytes()
        meta, body = parse_frontmatter(old.decode("utf-8"))
        if meta.get("origin") != "ai" or meta.get("status") != "draft":
            raise ReviewError("STALE_REVIEW_DRAFT", "draft state changed during review")
        data = _plain(report)
        if not isinstance(data, dict) or data.get("chapter") != run.chapter:
            raise ReviewError("INVALID_REVIEW_REPORT", "report chapter mismatch")
        verdict = data.get("verdict")
        if verdict not in {"PASS", "NEEDS_WORK"}:
            raise ReviewError("INVALID_REVIEW_REPORT", "invalid verdict")
        issues = data.get("issues", [])
        if any(isinstance(x, dict) and x.get("severity") in {"BLOCKER", "MAJOR"} for x in issues):
            verdict = "NEEDS_WORK"
            data["verdict"] = verdict
        if verdict == "PASS":
            new_meta = dict(meta); new_meta["status"] = "ready"; new_meta["updated_at"] = _now()
            rendered = build_frontmatter(new_meta) + body
        else:
            rendered = old.decode("utf-8")
        final_draft_revision = revision_sha256(rendered.encode("utf-8"))
        artifact = data | {
            "chapter": run.chapter, "draft_revision": final_draft_revision,
            "reviewed_at": _now(), "reviewer_model": run.reviewer_model,
            "context_hash": run.context_hash,
        }
        artifact["report_hash"] = _report_payload_hash(artifact)
        report_text = _canonical(artifact) + "\n"
        if len(report_text.encode("utf-8")) > REPORT_LIMIT_BYTES:
            raise ReviewError("REVIEW_REPORT_TOO_LARGE", "report exceeds size limit")
        rel = report_rel(run.chapter)
        metadata = {"agent_id": "reviewer", "content_kind": "review",
                    "old_revision": run.draft_revision, "new_revision": final_draft_revision,
                    "old_report_revision": run.report_revision, "verdict": verdict,
                    "context_hash": run.context_hash, "reviewer_model": run.reviewer_model}
        try:
            snap = self.snapshot_factory(self.project, f"ai.review.{verdict.lower()}",
                                         [f"drafts/{path.name}", rel], metadata=metadata)
        except Exception as exc:
            raise ReviewError("REVIEW_SNAPSHOT_FAILED", "could not prepare review snapshot") from exc
        # Recheck after snapshot preparation to close its race window.
        if file_revision(path) != run.draft_revision or file_revision(rp) != run.report_revision:
            snap.discard()
            code = "STALE_REVIEW_DRAFT" if file_revision(path) != run.draft_revision else "STALE_REVIEW_REPORT"
            raise ReviewError(code, "target changed while preparing snapshot")
        old_report = rp.read_bytes() if rp.is_file() else None
        try:
            self.writer(path, rendered)
            self.writer(rp, report_text)
            if path.read_bytes() != rendered.encode("utf-8") or rp.read_bytes() != report_text.encode("utf-8"):
                raise ReviewError("REVIEW_VERIFY_FAILED", "post-write verification failed")
            if parse_frontmatter(path.read_text(encoding="utf-8"))[1] != body:
                raise ReviewError("REVIEW_BODY_CHANGED", "review transaction changed prose")
            load_review_artifact(self.project, run.chapter)
            snap.commit()
        except Exception as exc:
            try:
                snap.restore()
                if path.read_bytes() != old or ((rp.read_bytes() if rp.is_file() else None) != old_report):
                    raise RuntimeError("rollback verification failed")
                snap.discard()
            except Exception as rollback:
                raise ReviewError("REVIEW_ROLLBACK_FAILED", "review rollback incomplete") from rollback
            if isinstance(exc, ReviewError):
                raise
            raise ReviewError("REVIEW_TRANSACTION_FAILED", "review failed; original bytes restored") from exc
        return ReviewResult(run.chapter, verdict, final_draft_revision, file_revision(rp),
                            artifact["report_hash"], rel, snap.seq)

    def reopen(self, *, chapter: int, expected_revision: str) -> ReopenResult:
        with chapter_lock(self.project, chapter):
            path = draft_path(self.project, chapter)
            if file_revision(path) != expected_revision:
                raise ReviewError("STALE_REVIEW_DRAFT", "draft changed before reopen")
            raw = path.read_bytes(); meta, body = parse_frontmatter(raw.decode("utf-8"))
            if meta.get("origin") != "ai" or meta.get("status") != "ready":
                raise ReviewError("INVALID_REOPEN_STATUS", "reopen requires an AI ready draft")
            new_meta = dict(meta); new_meta["status"] = "draft"; new_meta["updated_at"] = _now()
            rendered = build_frontmatter(new_meta) + body
            try:
                snap = self.snapshot_factory(self.project, "ai.review.reopen", [f"drafts/{path.name}"],
                                             metadata={"agent_id": "user", "content_kind": "review",
                                                       "old_revision": expected_revision})
            except Exception as exc:
                raise ReviewError("REVIEW_SNAPSHOT_FAILED", "could not prepare reopen snapshot") from exc
            if file_revision(path) != expected_revision:
                snap.discard(); raise ReviewError("STALE_REVIEW_DRAFT", "draft changed during reopen")
            try:
                self.writer(path, rendered)
                if path.read_bytes() != rendered.encode("utf-8"):
                    raise ReviewError("REVIEW_VERIFY_FAILED", "reopen post-write verification failed")
                if parse_frontmatter(path.read_text(encoding="utf-8"))[1] != body:
                    raise ReviewError("REVIEW_BODY_CHANGED", "reopen changed prose")
                snap.commit()
            except Exception as exc:
                try:
                    snap.restore()
                    if path.read_bytes() != raw:
                        raise RuntimeError("reopen rollback verification failed")
                    snap.discard()
                except Exception as rollback:
                    raise ReviewError("REVIEW_ROLLBACK_FAILED", "reopen rollback incomplete") from rollback
                if isinstance(exc, ReviewError): raise
                raise ReviewError("REVIEW_TRANSACTION_FAILED", "reopen failed; restored") from exc
            return ReopenResult(chapter, file_revision(path), snap.seq)

    def recover(self, *, chapter: int) -> None:
        # Architecture B persists no REVIEWING state or pending sidecar. A process
        # crash therefore leaves canonical bytes untouched and nothing to recover.
        raise ReviewError("NO_PENDING_REVIEW", f"no persisted pending review for chapter {chapter}")

    def inspect(self, *, chapter: int) -> ReviewInspection:
        artifact = load_review_artifact(self.project, chapter)
        draft_rev = file_revision(draft_path(self.project, chapter))
        return ReviewInspection(chapter, artifact, draft_rev,
                                file_revision(report_path(self.project, chapter)),
                                artifact["draft_revision"] == draft_rev)
