"""Minimal, privacy-safe persistent state for the M7 compose orchestrator."""
from __future__ import annotations

import dataclasses
import json
import re
from pathlib import Path
from typing import Any

from .chapter import confirmed_path, draft_path, parse_frontmatter
from .generation import generation_paths
from .mutation import ABSENT, file_revision
from .review import ReviewError, load_review_artifact, require_current_pass_report
from .storage import StorageError, atomic_write_json, format_chapter_filename


COMPOSE_FINAL_STATES = frozenset({"READY", "ESCALATED", "INTERRUPTED", "BLOCKED"})
COMPOSE_PHASES = frozenset({
    "INITIAL_WRITE",
    "WAITING_REVIEW",
    "WAITING_REWRITE",
    "WRITER_INTERRUPTED",
    "WAITING_REREVIEW",
    *COMPOSE_FINAL_STATES,
})
COMPOSE_WRITER_MODES = frozenset({"", "new", "rewrite", "resume"})
COMPOSE_VERDICTS = frozenset({"", "PASS", "NEEDS_WORK"})
MODEL_ROLES = frozenset({"chief", "writer", "reviewer"})
_HEX = re.compile(r"^[0-9a-f]{64}$")
_RUN_ID = re.compile(r"^[0-9a-f]{16,64}$")
_TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$")


class ComposeStateError(Exception):
    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")


def _invalid(message: str) -> None:
    raise ComposeStateError("INVALID_COMPOSE_RUN", message)


def _is_hash(value: str, *, allow_empty: bool = True, allow_absent: bool = False) -> bool:
    return ((allow_empty and value == "") or (allow_absent and value == ABSENT)
            or bool(_HEX.fullmatch(value)))


@dataclasses.dataclass(frozen=True)
class ComposeRunState:
    """The exact persisted allowlist. It intentionally contains no creative text."""

    chapter: int
    run_id: str
    phase: str
    max_rounds: int
    review_round: int
    draft_revision: str
    latest_report_hash: str
    latest_verdict: str
    issue_fingerprints: tuple[str, ...] | list[str]
    started_at: str
    updated_at: str
    writer_mode: str
    models: dict[str, str]
    initial_instruction_hash: str

    def __post_init__(self) -> None:
        if not isinstance(self.chapter, int) or isinstance(self.chapter, bool) or self.chapter < 1:
            _invalid("chapter must be a positive integer")
        if not isinstance(self.run_id, str) or not _RUN_ID.fullmatch(self.run_id):
            _invalid("run_id must be a bounded hexadecimal identifier")
        if self.phase not in COMPOSE_PHASES:
            _invalid("unknown compose phase")
        if (not isinstance(self.max_rounds, int) or isinstance(self.max_rounds, bool)
                or not 1 <= self.max_rounds <= 10):
            _invalid("max_rounds must be an integer from 1 to 10")
        if (not isinstance(self.review_round, int) or isinstance(self.review_round, bool)
                or not 0 <= self.review_round <= self.max_rounds):
            _invalid("review_round must be between zero and max_rounds")
        if not isinstance(self.draft_revision, str) or not _is_hash(
                self.draft_revision, allow_empty=True, allow_absent=True):
            _invalid("draft_revision is invalid")
        if not isinstance(self.latest_report_hash, str) or not _is_hash(self.latest_report_hash):
            _invalid("latest_report_hash is invalid")
        if self.latest_verdict not in COMPOSE_VERDICTS:
            _invalid("latest_verdict is invalid")
        if not isinstance(self.issue_fingerprints, (list, tuple)):
            _invalid("issue_fingerprints must be an array")
        fingerprints = tuple(self.issue_fingerprints)
        if len(fingerprints) > 20 or len(set(fingerprints)) != len(fingerprints):
            _invalid("issue_fingerprints must contain at most 20 unique values")
        if any(not isinstance(value, str) or not _HEX.fullmatch(value) for value in fingerprints):
            _invalid("issue_fingerprints must contain SHA-256 values")
        object.__setattr__(self, "issue_fingerprints", fingerprints)
        for name in ("started_at", "updated_at"):
            value = getattr(self, name)
            if not isinstance(value, str) or not _TIMESTAMP.fullmatch(value):
                _invalid(f"{name} is invalid")
        if self.writer_mode not in COMPOSE_WRITER_MODES:
            _invalid("writer_mode is invalid")
        if not isinstance(self.models, dict) or set(self.models) != MODEL_ROLES:
            _invalid("models must contain exactly chief, writer, and reviewer")
        models = dict(self.models)
        if any(not isinstance(value, str) or len(value) > 200 or any(
                char in value for char in "\r\n\x00") for value in models.values()):
            _invalid("model identifiers are invalid")
        object.__setattr__(self, "models", models)
        if (not isinstance(self.initial_instruction_hash, str)
                or not _HEX.fullmatch(self.initial_instruction_hash)):
            _invalid("initial_instruction_hash must be SHA-256")

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


_RUN_FIELDS = frozenset(field.name for field in dataclasses.fields(ComposeRunState))


class ComposeRunStore:
    def __init__(self, project, chapter: int):
        if not isinstance(chapter, int) or isinstance(chapter, bool) or chapter < 1:
            raise ComposeStateError("INVALID_COMPOSE_CHAPTER", "chapter must be positive")
        self.project = project
        self.chapter = chapter
        rel = f"workflow/.runs/{format_chapter_filename(chapter)}.compose.json"
        self.path = self._safe_run_path(rel)

    def _safe_run_path(self, rel: str) -> Path:
        lexical = self.project.dir / Path(rel)
        current = self.project.dir
        try:
            for part in Path(rel).parts:
                current = current / part
                if current.is_symlink():
                    raise ComposeStateError("UNSAFE_COMPOSE_RUN_PATH", "run path contains a symlink")
            return self.project.store.safe_path(self.project.id, rel)
        except ComposeStateError:
            raise
        except (OSError, StorageError) as exc:
            raise ComposeStateError("UNSAFE_COMPOSE_RUN_PATH", "run path is unsafe") from exc

    def exists(self) -> bool:
        return self.path.is_file()

    def load(self) -> ComposeRunState | None:
        if not self.path.exists():
            return None
        if not self.path.is_file() or self.path.is_symlink():
            raise ComposeStateError("UNSAFE_COMPOSE_RUN_PATH", "run sidecar is not a regular file")
        try:
            raw = self.path.read_bytes()
            if len(raw) > 64 * 1024:
                raise ComposeStateError("INVALID_COMPOSE_RUN_JSON", "run sidecar exceeds size limit")
            data = json.loads(raw.decode("utf-8"))
        except ComposeStateError:
            raise
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ComposeStateError("INVALID_COMPOSE_RUN_JSON", "run sidecar is not valid UTF-8 JSON") from exc
        if not isinstance(data, dict) or set(data) != _RUN_FIELDS:
            raise ComposeStateError("INVALID_COMPOSE_RUN", "run sidecar fields do not match allowlist")
        try:
            state = ComposeRunState(**data)
        except ComposeStateError:
            raise
        except (TypeError, ValueError) as exc:
            raise ComposeStateError("INVALID_COMPOSE_RUN", "run sidecar schema is invalid") from exc
        if state.chapter != self.chapter:
            raise ComposeStateError(
                "COMPOSE_RUN_CHAPTER_MISMATCH", "run sidecar chapter does not match its path")
        return state

    def require(self) -> ComposeRunState:
        state = self.load()
        if state is None:
            raise ComposeStateError("COMPOSE_RUN_NOT_FOUND", "compose run sidecar is missing")
        return state

    def save(self, state: ComposeRunState) -> None:
        if not isinstance(state, ComposeRunState):
            raise ComposeStateError("INVALID_COMPOSE_RUN", "state must be ComposeRunState")
        if state.chapter != self.chapter:
            raise ComposeStateError(
                "COMPOSE_RUN_CHAPTER_MISMATCH", "cannot save a run under another chapter")
        self._safe_run_path(f"workflow/.runs/{format_chapter_filename(self.chapter)}.compose.json")
        try:
            atomic_write_json(self.path, state.to_dict())
        except StorageError as exc:
            raise ComposeStateError("COMPOSE_RUN_WRITE_FAILED", "could not save compose run") from exc

    def reset(self) -> bool:
        self._safe_run_path(f"workflow/.runs/{format_chapter_filename(self.chapter)}.compose.json")
        if not self.path.exists():
            return False
        if not self.path.is_file() or self.path.is_symlink():
            raise ComposeStateError("UNSAFE_COMPOSE_RUN_PATH", "run sidecar is not a regular file")
        try:
            self.path.unlink()
        except OSError as exc:
            raise ComposeStateError("COMPOSE_RUN_RESET_FAILED", "could not reset compose run") from exc
        return True


@dataclasses.dataclass(frozen=True)
class ComposeStatus:
    chapter: int
    chapter_state: str
    draft_revision: str
    review_current: bool
    latest_verdict: str
    review_rounds: int
    compose_phase: str
    partial_exists: bool
    can_resume: bool
    can_confirm: bool


def compose_status(project, chapter: int) -> ComposeStatus:
    """Derive status from canonical files plus the minimal orchestration sidecar."""
    run = ComposeRunStore(project, chapter).load()
    path = draft_path(project, chapter)
    confirmed = confirmed_path(project, chapter)
    partial, partial_sidecar = generation_paths(project, chapter)
    partial_exists = partial.is_file() or partial_sidecar.is_file()
    phase = run.phase if run else ""
    rounds = run.review_round if run else 0

    if confirmed.exists() and path.exists():
        chapter_state, revision, origin, draft_status = "BLOCKED", file_revision(path), "", ""
    elif confirmed.exists():
        chapter_state, revision, origin, draft_status = "ALREADY_CONFIRMED", ABSENT, "", "confirmed"
    elif not path.exists():
        chapter_state, revision, origin, draft_status = "NO_DRAFT", ABSENT, "", ""
    else:
        revision = file_revision(path)
        try:
            meta, _ = parse_frontmatter(path.read_text(encoding="utf-8"))
            origin, draft_status = str(meta.get("origin", "")), str(meta.get("status", ""))
            if origin != "ai":
                chapter_state = "MANUAL_DRAFT_PROTECTED"
            elif draft_status == "ready":
                chapter_state = "READY"
            elif draft_status == "draft":
                chapter_state = "DRAFT"
            else:
                chapter_state = "BLOCKED"
        except Exception:
            chapter_state, origin, draft_status = "BLOCKED", "", ""

    current = False
    verdict = ""
    if path.exists():
        try:
            artifact = load_review_artifact(project, chapter)
            verdict = artifact["verdict"]
            current = artifact["draft_revision"] == revision
        except ReviewError:
            pass

    if run is None:
        can_resume = False
    elif phase == "WRITER_INTERRUPTED":
        can_resume = partial.is_file() and partial_sidecar.is_file()
    else:
        can_resume = phase != "READY"
    can_confirm = False
    if origin == "ai" and draft_status == "ready" and not confirmed.exists():
        try:
            require_current_pass_report(project, chapter, revision)
            can_confirm = True
        except ReviewError:
            pass
    return ComposeStatus(
        chapter, chapter_state, revision, current, verdict, rounds, phase,
        partial_exists, can_resume, can_confirm,
    )
