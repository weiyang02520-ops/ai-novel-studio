"""Bounded M7 Chief -> Writer -> Reviewer orchestration core."""
from __future__ import annotations

import dataclasses
import datetime
import hashlib
from typing import Callable

from llm.types import Usage

from .chapter import confirmed_path, draft_path, parse_frontmatter
from .mutation import file_revision
from .review import ReviewError, load_review_artifact, require_current_pass_report
from .review_workflow import ReviewWorkflowRequest
from .revision_feedback import (
    build_revision_feedback,
    is_rewriteable_review,
    is_stalled_review,
    major_blocker_fingerprints,
    non_rewriteable_preflight_codes,
)
from .write_workflow import WriteRequest


FINAL_STATES = frozenset({"READY", "ESCALATED", "INTERRUPTED", "BLOCKED"})
STALE_CODES = frozenset({
    "STALE_REVISION_FEEDBACK", "STALE_DRAFT_REVISION",
    "STALE_REVIEW_DRAFT", "STALE_REVIEW_REPORT",
})


class CreationWorkflowError(Exception):
    def __init__(self, code: str, message: str):
        self.code, self.message = code, message
        super().__init__(f"{code}: {message}")


@dataclasses.dataclass
class CreationRequest:
    project: object
    chapter: int | None = None
    instruction: str = ""
    title: str = ""
    target_chars: int = 4000
    characters: list[str] = dataclasses.field(default_factory=list)
    world: list[str] = dataclasses.field(default_factory=list)
    max_review_rounds: int | None = None
    review_instruction: str = ""
    resume: bool = False
    stream: bool = True


@dataclasses.dataclass(frozen=True)
class CreationRound:
    round_number: int
    draft_revision_before: str
    draft_revision_after: str
    review_report_hash: str
    review_verdict: str
    review_issue_counts: dict[str, int]
    writer_mode: str
    writer_model: str
    reviewer_model: str
    started_at: str = ""
    finished_at: str = ""


@dataclasses.dataclass
class CreationResult:
    chapter: int
    status: str
    rounds_completed: int
    draft_revision: str
    latest_report_hash: str
    latest_verdict: str
    final_state: str
    rounds: list[CreationRound] = dataclasses.field(default_factory=list)
    chief_usages: list[Usage] = dataclasses.field(default_factory=list)
    writer_usages: list[Usage] = dataclasses.field(default_factory=list)
    reviewer_usages: list[Usage] = dataclasses.field(default_factory=list)
    warnings: list[str] = dataclasses.field(default_factory=list)
    reason: str = ""


def _body(project, chapter: int) -> str:
    _, body = parse_frontmatter(draft_path(project, chapter).read_text(encoding="utf-8"))
    return body


def _body_hash(project, chapter: int) -> str:
    return hashlib.sha256(_body(project, chapter).encode("utf-8")).hexdigest()


def _counts(report) -> dict[str, int]:
    return {severity: sum(x.severity == severity for x in report.issues)
            for severity in ("BLOCKER", "MAJOR", "MINOR", "INFO")}


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


class CreationWorkflow:
    def __init__(self, *, write_workflow_factory: Callable[[object], object],
                 review_workflow_factory: Callable[[object], object], settings=None,
                 state_store_factory=None):
        self.write_workflow_factory = write_workflow_factory
        self.review_workflow_factory = review_workflow_factory
        self.settings = settings
        self.state_store_factory = state_store_factory  # reserved for resume integration

    def _result(self, chapter, status, final_state, *, rounds=None, chief=None, writer=None,
                reviewer=None, warnings=None, reason="", report_hash="", verdict=""):
        revision = file_revision(draft_path(self._project, chapter))
        return CreationResult(chapter, status, len(rounds or []), revision, report_hash,
                              verdict, final_state, rounds or [], chief or [], writer or [],
                              reviewer or [], warnings or [], reason)

    def _stale_result(self, exc, chapter, *, rounds, chief, writer, reviewer, warnings):
        code = getattr(exc, "code", "")
        if code not in STALE_CODES:
            raise exc
        return self._result(chapter, "BLOCKED", "BLOCKED", rounds=rounds,
                            chief=chief, writer=writer, reviewer=reviewer,
                            warnings=warnings, reason=code)

    def run(self, request: CreationRequest, *, on_stage=None) -> CreationResult:
        project = self._project = request.project
        chapter = request.chapter or project.current_chapter + 1
        if not isinstance(chapter, int) or isinstance(chapter, bool) or chapter < 1:
            raise CreationWorkflowError("INVALID_COMPOSE_CHAPTER", "chapter must be positive")
        configured = ((getattr(self.settings, "workflow", {}) or {}).get("max_review_rounds", 3)
                      if self.settings is not None else 3)
        max_rounds = request.max_review_rounds if request.max_review_rounds is not None else configured
        if not isinstance(max_rounds, int) or isinstance(max_rounds, bool) or not 1 <= max_rounds <= 10:
            raise CreationWorkflowError("INVALID_MAX_REVIEW_ROUNDS", "max rounds must be 1..10")
        if request.resume:
            raise CreationWorkflowError("RESUME_NOT_IMPLEMENTED", "resume integration is separate")
        if confirmed_path(project, chapter).exists():
            return self._result(chapter, "ALREADY_CONFIRMED", "BLOCKED")

        target = draft_path(project, chapter)
        if target.exists():
            meta, _ = parse_frontmatter(target.read_text(encoding="utf-8"))
            if meta.get("origin") != "ai":
                return self._result(chapter, "MANUAL_DRAFT_PROTECTED", "BLOCKED")
            if meta.get("status") == "ready":
                try:
                    artifact = require_current_pass_report(project, chapter, file_revision(target))
                except ReviewError:
                    return self._result(chapter, "READY_REVIEW_INVALID", "BLOCKED",
                                        reason="STALE_WORKFLOW_STATE")
                return self._result(chapter, "READY", "READY",
                                    report_hash=artifact["report_hash"], verdict="PASS")
            if meta.get("status") != "draft":
                return self._result(chapter, "INVALID_DRAFT_STATE", "BLOCKED")

        writer_flow = self.write_workflow_factory(project)
        review_flow = self.review_workflow_factory(project)
        rounds: list[CreationRound] = []
        chief_usages: list[Usage] = []
        writer_usages: list[Usage] = []
        reviewer_usages: list[Usage] = []
        warnings: list[str] = []
        writer_mode, writer_model = "", ""
        previous_fingerprints: tuple[str, ...] = ()

        if not target.exists():
            try:
                written = writer_flow.run(WriteRequest(
                    project, chapter, request.instruction, request.title, request.target_chars,
                    request.characters, request.world, "new", stream=request.stream))
            except Exception as exc:
                return self._stale_result(exc, chapter, rounds=rounds, chief=chief_usages,
                                          writer=writer_usages, reviewer=reviewer_usages,
                                          warnings=warnings)
            chief_usages.extend(written.chief_usages); writer_usages.extend(written.writer_usages)
            warnings.extend(getattr(written, "warnings", []))
            if written.status == "interrupted":
                return self._result(chapter, "WRITER_INTERRUPTED", "INTERRUPTED",
                                    chief=chief_usages, writer=writer_usages, warnings=warnings)
            writer_mode, writer_model = "new", getattr(written.writer_result, "model", "")

        # A current NEEDS_WORK artifact may be consumed without spending another review call.
        pending = None
        try:
            artifact = load_review_artifact(project, chapter)
            if artifact["draft_revision"] == file_revision(target) and artifact["verdict"] == "NEEDS_WORK":
                from agents.review_report import parse_review_report
                import json
                fields = {k: artifact[k] for k in (
                    "chapter", "verdict", "summary", "issues", "strengths", "task_fulfillment",
                    "continuity_assessment", "style_assessment", "logic_assessment", "confidence", "source")}
                pending = (parse_review_report(json.dumps(fields, ensure_ascii=False)).report,
                           artifact["report_hash"], artifact["draft_revision"])
        except ReviewError:
            pass

        if pending is not None:
            report, report_hash, draft_revision = pending
            previous_fingerprints = major_blocker_fingerprints(report)
            existing_preflight = type("ExistingPreflight", (), {"blockers": ()})()
            if not is_rewriteable_review(report, existing_preflight):
                return self._result(chapter, "ESCALATED", "ESCALATED",
                                    chief=chief_usages, writer=writer_usages,
                                    reviewer=reviewer_usages, warnings=warnings,
                                    reason="NON_REWRITEABLE_BLOCKER", report_hash=report_hash,
                                    verdict="NEEDS_WORK")
            feedback = build_revision_feedback(
                report, review_report_hash=report_hash, draft_revision=draft_revision,
                round_number=1)
            before_body = _body_hash(project, chapter)
            try:
                written = writer_flow.run(WriteRequest(
                    project, chapter, request.instruction, request.title, request.target_chars,
                    request.characters, request.world, "rewrite", stream=request.stream,
                    revision_feedback=feedback))
            except Exception as exc:
                return self._stale_result(exc, chapter, rounds=rounds, chief=chief_usages,
                                          writer=writer_usages, reviewer=reviewer_usages,
                                          warnings=warnings)
            chief_usages.extend(written.chief_usages); writer_usages.extend(written.writer_usages)
            warnings.extend(getattr(written, "warnings", []))
            if written.status == "interrupted":
                return self._result(chapter, "WRITER_INTERRUPTED", "INTERRUPTED",
                                    chief=chief_usages, writer=writer_usages, warnings=warnings,
                                    report_hash=report_hash, verdict="NEEDS_WORK")
            if _body_hash(project, chapter) == before_body:
                return self._result(chapter, "ESCALATED", "ESCALATED",
                                    chief=chief_usages, writer=writer_usages, warnings=warnings,
                                    reason="WRITER_NO_EFFECT", report_hash=report_hash,
                                    verdict="NEEDS_WORK")
            writer_mode, writer_model = "rewrite", getattr(written.writer_result, "model", "")
            pending = None

        for review_round in range(1, max_rounds + 1):
            round_started = _now()
            try:
                reviewed = review_flow.run(ReviewWorkflowRequest(
                    project, chapter, request.review_instruction,
                    request.characters, request.world))
            except Exception as exc:
                return self._stale_result(exc, chapter, rounds=rounds, chief=chief_usages,
                                          writer=writer_usages, reviewer=reviewer_usages,
                                          warnings=warnings)
            reviewer_usages.extend(reviewed.usages)
            report = reviewed.report
            report_hash = reviewed.review_result.report_hash
            draft_revision = reviewed.review_result.draft_revision
            reviewer_model = getattr(reviewed.reviewer_result, "model", "")
            preflight = reviewed.preflight
            before_review = getattr(reviewed.reviewer_result, "draft_revision", draft_revision)
            rounds.append(CreationRound(
                review_round, before_review, draft_revision, report_hash, report.verdict,
                _counts(report), writer_mode, writer_model, reviewer_model,
                round_started, _now()))

            if report.verdict == "PASS":
                return self._result(chapter, "READY", "READY", rounds=rounds,
                                    chief=chief_usages, writer=writer_usages,
                                    reviewer=reviewer_usages, warnings=warnings,
                                    report_hash=report_hash, verdict="PASS")
            codes = non_rewriteable_preflight_codes(preflight)
            if codes:
                reason = ("REVIEW_CONTEXT_INSUFFICIENT" if "DRAFT_TRUNCATED_FOR_REVIEW" in codes
                          else "NON_REWRITEABLE_BLOCKER")
                return self._result(chapter, "ESCALATED", "ESCALATED", rounds=rounds,
                                    chief=chief_usages, writer=writer_usages,
                                    reviewer=reviewer_usages, warnings=warnings, reason=reason,
                                    report_hash=report_hash, verdict="NEEDS_WORK")
            fingerprints = major_blocker_fingerprints(report)
            if is_stalled_review(previous_fingerprints, fingerprints):
                return self._result(chapter, "ESCALATED", "ESCALATED", rounds=rounds,
                                    chief=chief_usages, writer=writer_usages,
                                    reviewer=reviewer_usages, warnings=warnings,
                                    reason="STALLED_REVIEW", report_hash=report_hash,
                                    verdict="NEEDS_WORK")
            previous_fingerprints = fingerprints
            if review_round == max_rounds:
                return self._result(chapter, "ESCALATED", "ESCALATED", rounds=rounds,
                                    chief=chief_usages, writer=writer_usages,
                                    reviewer=reviewer_usages, warnings=warnings,
                                    reason="MAX_REVIEW_ROUNDS", report_hash=report_hash,
                                    verdict="NEEDS_WORK")
            if not is_rewriteable_review(report, preflight):
                return self._result(chapter, "ESCALATED", "ESCALATED", rounds=rounds,
                                    chief=chief_usages, writer=writer_usages,
                                    reviewer=reviewer_usages, warnings=warnings,
                                    reason="NON_REWRITEABLE_BLOCKER", report_hash=report_hash,
                                    verdict="NEEDS_WORK")
            feedback = build_revision_feedback(
                report, review_report_hash=report_hash, draft_revision=draft_revision,
                round_number=review_round)
            before_revision, before_body = file_revision(target), _body_hash(project, chapter)
            try:
                written = writer_flow.run(WriteRequest(
                    project, chapter, request.instruction, request.title, request.target_chars,
                    request.characters, request.world, "rewrite", stream=request.stream,
                    revision_feedback=feedback))
            except Exception as exc:
                return self._stale_result(exc, chapter, rounds=rounds, chief=chief_usages,
                                          writer=writer_usages, reviewer=reviewer_usages,
                                          warnings=warnings)
            chief_usages.extend(written.chief_usages); writer_usages.extend(written.writer_usages)
            warnings.extend(getattr(written, "warnings", []))
            if written.status == "interrupted":
                return self._result(chapter, "WRITER_INTERRUPTED", "INTERRUPTED", rounds=rounds,
                                    chief=chief_usages, writer=writer_usages,
                                    reviewer=reviewer_usages, warnings=warnings)
            if _body_hash(project, chapter) == before_body:
                return self._result(chapter, "ESCALATED", "ESCALATED", rounds=rounds,
                                    chief=chief_usages, writer=writer_usages,
                                    reviewer=reviewer_usages, warnings=warnings,
                                    reason="WRITER_NO_EFFECT", report_hash=report_hash,
                                    verdict="NEEDS_WORK")
            writer_mode, writer_model = "rewrite", getattr(written.writer_result, "model", "")
            if rounds:
                rounds[-1] = dataclasses.replace(rounds[-1],
                    draft_revision_before=before_revision,
                    draft_revision_after=file_revision(target))

        raise AssertionError("bounded review loop exhausted unexpectedly")
