"""Read-only deterministic checks performed before an AI draft review."""
from __future__ import annotations

import dataclasses
from typing import Any, Iterable

from .chapter import confirmed_path, draft_path, parse_frontmatter
from .knowledge import doctor
from .mutation import file_revision


@dataclasses.dataclass(frozen=True)
class PreflightIssue:
    severity: str
    code: str
    message: str
    source: str = ""


@dataclasses.dataclass(frozen=True)
class ReviewPreflightResult:
    chapter: int
    draft_revision: str | None
    issues: tuple[PreflightIssue, ...]
    can_review: bool
    can_ready: bool

    @property
    def blockers(self) -> tuple[PreflightIssue, ...]:
        return tuple(x for x in self.issues if x.severity == "BLOCKER")


def merge_preflight_issues(preflight: Iterable[PreflightIssue], llm_issues: Iterable[Any]) -> list[Any]:
    """Keep deterministic issues first; model output cannot erase them."""
    return [*preflight, *llm_issues]


class ReviewPreflight:
    _FATAL = {
        "DRAFT_NOT_FOUND", "INVALID_UTF8", "INVALID_DRAFT",
        "DRAFT_CHAPTER_MISMATCH", "MANUAL_DRAFT_NOT_REVIEWABLE",
        "INVALID_DRAFT_STATUS", "CONFIRMED_CHAPTER_CONFLICT",
    }

    def run(self, project, chapter: int, *, allow_reviewing: bool = False) -> ReviewPreflightResult:
        issues: list[PreflightIssue] = []
        seen: set[tuple[str, str, str]] = set()

        def add(severity: str, code: str, message: str, source: str = "") -> None:
            key = (severity, code, source)
            if key not in seen:
                seen.add(key)
                issues.append(PreflightIssue(severity, code, message, source))

        path = draft_path(project, chapter)
        revision = file_revision(path) if path.is_file() else None
        meta = None
        body = None
        if not path.is_file():
            add("BLOCKER", "DRAFT_NOT_FOUND", f"AI draft chapter {chapter} does not exist",
                f"drafts/{path.name}")
        else:
            try:
                text = path.read_bytes().decode("utf-8", errors="strict")
            except UnicodeDecodeError:
                add("BLOCKER", "INVALID_UTF8", "Draft is not valid UTF-8", f"drafts/{path.name}")
            except OSError as exc:
                add("BLOCKER", "INVALID_DRAFT", str(exc), f"drafts/{path.name}")
            else:
                try:
                    meta, body = parse_frontmatter(text)
                except Exception as exc:
                    add("BLOCKER", "INVALID_DRAFT", str(exc), f"drafts/{path.name}")

        if meta is not None:
            if int(meta.get("chapter", -1)) != chapter:
                add("BLOCKER", "DRAFT_CHAPTER_MISMATCH",
                    f"frontmatter chapter={meta.get('chapter')} does not match {chapter}",
                    f"drafts/{path.name}")
            if meta.get("origin") != "ai":
                add("BLOCKER", "MANUAL_DRAFT_NOT_REVIEWABLE",
                    "Reviewer accepts only origin=ai drafts", f"drafts/{path.name}")
            allowed = {"draft", "reviewing"} if allow_reviewing else {"draft"}
            if meta.get("status") not in allowed:
                add("BLOCKER", "INVALID_DRAFT_STATUS",
                    f"status={meta.get('status')!r}, allowed={sorted(allowed)}",
                    f"drafts/{path.name}")
            if meta.get("generation_state") not in {"complete", "truncated"}:
                add("BLOCKER", "INVALID_GENERATION_STATE",
                    f"generation_state={meta.get('generation_state')!r}",
                    f"drafts/{path.name}")
            if body is not None and not body.strip():
                add("BLOCKER", "EMPTY_DRAFT_BODY", "Draft body is empty", f"drafts/{path.name}")

        confirmed = confirmed_path(project, chapter)
        if confirmed.exists():
            add("BLOCKER", "CONFIRMED_CHAPTER_CONFLICT",
                "A confirmed chapter with the same number already exists",
                f"chapters/{confirmed.name}")

        outline_rel = f"outline/chapters/ch{chapter:04d}.md"
        if not project.store.safe_path(project.id, outline_rel).is_file():
            add("WARNING", "MISSING_CHAPTER_OUTLINE",
                "Chapter outline is missing; review context is limited", outline_rel)

        try:
            doctor_issues = doctor(project)
        except Exception as exc:
            add("BLOCKER", "DOCTOR_FAILED", str(exc))
        else:
            for issue in doctor_issues:
                severity = "BLOCKER" if issue.get("severity") == "ERROR" else "WARNING"
                add(severity, str(issue.get("code", "DOCTOR_ISSUE")),
                    str(issue.get("message", "")))

        blockers = [x for x in issues if x.severity == "BLOCKER"]
        fatal = any(x.code in self._FATAL for x in blockers)
        return ReviewPreflightResult(chapter, revision, tuple(issues), not fatal, not blockers)
