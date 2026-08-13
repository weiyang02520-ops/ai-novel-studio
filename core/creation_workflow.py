"""Bounded M7 Chief -> Writer -> Reviewer orchestration core."""
from __future__ import annotations

import dataclasses
import datetime
import hashlib
import uuid
from typing import Callable

from llm.types import Usage

from .chapter import confirmed_path, draft_path, parse_frontmatter
from .compose_state import ComposeRunState, ComposeRunStore, ComposeStateError
from .context_budget import ContextBudgetError
from .generation import GenerationWorkspace
from .mutation import file_revision
from .review import ReviewError, load_review_artifact, require_current_pass_report
from .review_preflight import PreflightIssue, ReviewPreflightResult
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


def _instruction_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _model_name(owner, provider_name: str) -> str:
    provider = getattr(owner, provider_name, None)
    return str(getattr(getattr(provider, "config", None), "model", "") or "")


def _report_from_artifact(artifact):
    from agents.review_report import parse_review_report
    import json
    fields = {key: artifact[key] for key in (
        "chapter", "verdict", "summary", "issues", "strengths", "task_fulfillment",
        "continuity_assessment", "style_assessment", "logic_assessment", "confidence",
        "source",
    )}
    return parse_review_report(json.dumps(fields, ensure_ascii=False)).report


def _existing_review_preflight(review_flow, request: ReviewWorkflowRequest):
    """Re-run deterministic checks before consuming a persisted NEEDS_WORK report."""
    adapter = getattr(review_flow, "preflight_existing", None)
    if callable(adapter):
        return adapter(request)
    runner = getattr(review_flow, "preflight", None)
    builder = getattr(review_flow, "context_builder", None)
    if runner is None or builder is None:
        raise CreationWorkflowError(
            "REVIEW_PREFLIGHT_UNAVAILABLE",
            "persisted review feedback cannot be consumed without deterministic preflight",
        )
    initial = runner.run(request.project, request.chapter)
    if not initial.can_review:
        return initial
    try:
        plan = builder.build(
            request.project, request.chapter, instruction=request.instruction,
            characters=request.characters, world=request.world,
            draft_revision=initial.draft_revision,
        )
    except ContextBudgetError:
        issue = PreflightIssue(
            "BLOCKER", "REVIEW_CONTEXT_IMPOSSIBLE",
            "Current draft and authoritative facts cannot fit review context",
        )
        return ReviewPreflightResult(
            request.chapter, initial.draft_revision, (issue,), False, False)
    return runner.run(request.project, request.chapter, context_plan=plan)


class CreationWorkflow:
    def __init__(self, *, write_workflow_factory: Callable[[object], object],
                 review_workflow_factory: Callable[[object], object], settings=None,
                 state_store_factory=None):
        self.write_workflow_factory = write_workflow_factory
        self.review_workflow_factory = review_workflow_factory
        self.settings = settings
        self.state_store_factory = state_store_factory or ComposeRunStore

    def _save_phase(self, phase: str, **changes) -> None:
        if self._run_state is None:
            return
        changes.setdefault("updated_at", _now())
        changes.setdefault("draft_revision", file_revision(
            draft_path(self._project, self._run_state.chapter)))
        self._run_state = dataclasses.replace(self._run_state, phase=phase, **changes)
        self._run_store.save(self._run_state)

    def _blocked(self, chapter: int, reason: str) -> CreationResult:
        # Validation failures are retryable and must not destroy the durable phase.
        return self._result(chapter, "BLOCKED", "BLOCKED", reason=reason)

    def _result(self, chapter, status, final_state, *, rounds=None, chief=None, writer=None,
                reviewer=None, warnings=None, reason="", report_hash="", verdict=""):
        revision = file_revision(draft_path(self._project, chapter))
        if getattr(self, "_run_store", None) is not None:
            if final_state == "READY":
                self._run_store.reset()
                self._run_state = None
            elif final_state == "ESCALATED" and self._run_state is not None:
                self._save_phase("ESCALATED", latest_report_hash=report_hash,
                                 latest_verdict=verdict)
        completed = max(len(rounds or []), getattr(self, "_completed_reviews", 0))
        return CreationResult(chapter, status, completed, revision, report_hash,
                              verdict, final_state, rounds or [], chief or [], writer or [],
                              reviewer or [], warnings or [], reason)

    def _stale_result(self, exc, chapter, *, rounds, chief, writer, reviewer, warnings):
        code = getattr(exc, "code", "")
        if code not in STALE_CODES:
            raise exc
        self._save_phase("BLOCKED")
        return self._result(chapter, "BLOCKED", "BLOCKED", rounds=rounds,
                            chief=chief, writer=writer, reviewer=reviewer,
                            warnings=warnings, reason=code)

    def run(self, request: CreationRequest, *, on_stage=None) -> CreationResult:
        project = self._project = request.project
        self._completed_reviews = 0
        chapter = request.chapter or project.current_chapter + 1
        if not isinstance(chapter, int) or isinstance(chapter, bool) or chapter < 1:
            raise CreationWorkflowError("INVALID_COMPOSE_CHAPTER", "chapter must be positive")
        configured = ((getattr(self.settings, "workflow", {}) or {}).get("max_review_rounds", 3)
                      if self.settings is not None else 3)
        max_rounds = request.max_review_rounds if request.max_review_rounds is not None else configured
        if not isinstance(max_rounds, int) or isinstance(max_rounds, bool) or not 1 <= max_rounds <= 10:
            raise CreationWorkflowError("INVALID_MAX_REVIEW_ROUNDS", "max rounds must be 1..10")
        self._run_store = self.state_store_factory(project, chapter)
        self._run_state = None
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
                self._run_store.reset()
                return self._result(chapter, "READY", "READY",
                                    report_hash=artifact["report_hash"], verdict="PASS")
            if meta.get("status") != "draft":
                return self._result(chapter, "INVALID_DRAFT_STATE", "BLOCKED")

        writer_flow = self.write_workflow_factory(project)
        review_flow = self.review_workflow_factory(project)
        models = {
            "chief": _model_name(writer_flow, "chief_provider"),
            "writer": _model_name(writer_flow, "writer_provider"),
            "reviewer": _model_name(review_flow, "provider"),
        }
        resume_state = None
        if request.resume:
            try:
                resume_state = self._run_store.require()
            except ComposeStateError as exc:
                raise CreationWorkflowError(exc.code, exc.message) from exc
            self._run_state = resume_state
            if (request.instruction
                    and resume_state.initial_instruction_hash != _instruction_hash(request.instruction)):
                return self._blocked(chapter, "COMPOSE_INSTRUCTION_MISMATCH")
            if request.max_review_rounds is not None and max_rounds != resume_state.max_rounds:
                return self._blocked(chapter, "COMPOSE_MAX_ROUNDS_MISMATCH")
            if request.max_review_rounds is None and configured != resume_state.max_rounds:
                return self._blocked(chapter, "COMPOSE_MAX_ROUNDS_MISMATCH")
            max_rounds = resume_state.max_rounds
            if models != resume_state.models:
                return self._blocked(chapter, "COMPOSE_MODEL_CONFIG_MISMATCH")
            if resume_state.phase in {"BLOCKED", "INTERRUPTED"}:
                return self._blocked(chapter, "COMPOSE_RUN_NOT_RESUMABLE")
        else:
            if self._run_store.exists():
                self._run_state = self._run_store.require()
                return self._blocked(chapter, "COMPOSE_RUN_EXISTS")
            started = _now()
            initial_report_hash = ""
            initial_verdict = ""
            initial_fingerprints: tuple[str, ...] = ()
            if target.exists():
                try:
                    existing_artifact = load_review_artifact(project, chapter)
                    if existing_artifact["draft_revision"] == file_revision(target):
                        initial_report_hash = existing_artifact["report_hash"]
                        initial_verdict = existing_artifact["verdict"]
                        initial_fingerprints = major_blocker_fingerprints(
                            _report_from_artifact(existing_artifact))
                except ReviewError:
                    pass
            self._run_state = ComposeRunState(
                chapter=chapter, run_id=uuid.uuid4().hex, phase="INITIAL_WRITE",
                max_rounds=max_rounds, review_round=0,
                draft_revision=file_revision(target), latest_report_hash=initial_report_hash,
                latest_verdict=initial_verdict, issue_fingerprints=initial_fingerprints, started_at=started,
                updated_at=started, writer_mode="new" if not target.exists() else "",
                models=models, initial_instruction_hash=_instruction_hash(request.instruction),
            )
            self._run_store.save(self._run_state)
        rounds: list[CreationRound] = []
        chief_usages: list[Usage] = []
        writer_usages: list[Usage] = []
        reviewer_usages: list[Usage] = []
        warnings: list[str] = []
        force_review = False
        writer_mode, writer_model = "", ""
        previous_fingerprints: tuple[str, ...] = (
            tuple(resume_state.issue_fingerprints) if resume_state else ())
        completed_reviews = resume_state.review_round if resume_state else 0
        self._completed_reviews = completed_reviews

        if resume_state is not None:
            current_revision = file_revision(target)
            workspace = GenerationWorkspace(project, chapter)
            if (resume_state.phase == "INITIAL_WRITE" and current_revision == resume_state.draft_revision
                    and workspace.partial.is_file() and workspace.sidecar.is_file()):
                try:
                    partial_meta = workspace.metadata()
                    partial_mode = partial_meta.get("mode")
                except Exception:
                    return self._blocked(chapter, "INVALID_PARTIAL_SIDECAR")
                if partial_mode not in {"new", "rewrite"}:
                    return self._blocked(chapter, "INVALID_PARTIAL_SIDECAR")
                resume_state = dataclasses.replace(
                    resume_state, phase="WRITER_INTERRUPTED", writer_mode=partial_mode,
                    updated_at=_now())
                self._run_state = resume_state
                self._run_store.save(resume_state)
            if resume_state.phase == "ESCALATED":
                # Explicit user resume starts a fresh bounded attempt after facts/manual work changed.
                resume_state = dataclasses.replace(
                    resume_state, phase="WAITING_REVIEW", review_round=0,
                    issue_fingerprints=(), updated_at=_now())
                self._run_state = resume_state
                self._run_store.save(resume_state)
                completed_reviews = 0
                self._completed_reviews = 0
                previous_fingerprints = ()
                force_review = True
            current_revision = file_revision(target)
            if current_revision != resume_state.draft_revision:
                if target.exists() and resume_state.phase in {"INITIAL_WRITE", "WRITER_INTERRUPTED"}:
                    next_phase = ("WAITING_REREVIEW" if resume_state.writer_mode == "rewrite"
                                  else "WAITING_REVIEW")
                    resume_state = dataclasses.replace(
                        resume_state, phase=next_phase, draft_revision=current_revision,
                        updated_at=_now())
                    self._run_state = resume_state
                    self._run_store.save(resume_state)
                elif target.exists() and resume_state.phase == "WAITING_REWRITE":
                    resume_state = dataclasses.replace(
                        resume_state, phase="WAITING_REREVIEW", draft_revision=current_revision,
                        updated_at=_now())
                    self._run_state = resume_state
                    self._run_store.save(resume_state)
                elif target.exists() and resume_state.phase in {"WAITING_REVIEW", "WAITING_REREVIEW"}:
                    # A user/external edit wins; the only safe continuation is to
                    # review the new canonical revision, never overwrite it.
                    resume_state = dataclasses.replace(
                        resume_state, draft_revision=current_revision,
                        issue_fingerprints=(), updated_at=_now())
                    self._run_state = resume_state
                    self._run_store.save(resume_state)
                    previous_fingerprints = ()
                else:
                    return self._blocked(chapter, "STALE_WORKFLOW_STATE")
            current_revision = file_revision(target)
            if resume_state.phase in {"WAITING_REVIEW", "WAITING_REREVIEW"}:
                try:
                    recovered_artifact = load_review_artifact(project, chapter)
                except ReviewError:
                    recovered_artifact = None
                if (recovered_artifact is not None
                        and recovered_artifact["draft_revision"] == current_revision
                        and recovered_artifact["report_hash"] != resume_state.latest_report_hash):
                    recovered_report = _report_from_artifact(recovered_artifact)
                    recovered_round = min(resume_state.max_rounds, resume_state.review_round + 1)
                    resume_state = dataclasses.replace(
                        resume_state,
                        phase=("WAITING_REWRITE" if recovered_artifact["verdict"] == "NEEDS_WORK"
                               else resume_state.phase),
                        review_round=recovered_round,
                        latest_report_hash=recovered_artifact["report_hash"],
                        latest_verdict=recovered_artifact["verdict"],
                        issue_fingerprints=major_blocker_fingerprints(recovered_report),
                        updated_at=_now(),
                    )
                    self._run_state = resume_state
                    self._run_store.save(resume_state)
                    completed_reviews = recovered_round
                    self._completed_reviews = recovered_round
            if resume_state.latest_report_hash:
                try:
                    stored_artifact = load_review_artifact(project, chapter)
                except ReviewError:
                    return self._blocked(chapter, "COMPOSE_REPORT_MISMATCH")
                if stored_artifact["report_hash"] != resume_state.latest_report_hash:
                    return self._blocked(chapter, "COMPOSE_REPORT_MISMATCH")
                current_report_required = (
                    resume_state.phase == "WAITING_REWRITE"
                    or (resume_state.phase == "WRITER_INTERRUPTED"
                        and resume_state.writer_mode == "rewrite"))
                if (current_report_required
                        and stored_artifact["draft_revision"] != current_revision):
                    return self._blocked(chapter, "COMPOSE_REPORT_MISMATCH")
            if resume_state.phase == "WRITER_INTERRUPTED":
                if not workspace.partial.is_file() or not workspace.sidecar.is_file():
                    return self._blocked(chapter, "COMPOSE_PARTIAL_NOT_FOUND")
                try:
                    partial_meta = workspace.metadata()
                except Exception:
                    return self._blocked(chapter, "INVALID_PARTIAL_SIDECAR")
                if (partial_meta.get("mode") != resume_state.writer_mode
                        or partial_meta.get("base_revision") != resume_state.draft_revision):
                    return self._blocked(chapter, "STALE_WORKFLOW_STATE")
                resume_feedback = None
                if resume_state.writer_mode == "rewrite":
                    try:
                        artifact = load_review_artifact(project, chapter)
                    except ReviewError:
                        return self._blocked(chapter, "COMPOSE_REPORT_MISMATCH")
                    if (artifact["verdict"] != "NEEDS_WORK"
                            or artifact["report_hash"] != resume_state.latest_report_hash
                            or artifact["draft_revision"] != resume_state.draft_revision):
                        return self._blocked(chapter, "COMPOSE_REPORT_MISMATCH")
                    resume_feedback = build_revision_feedback(
                        _report_from_artifact(artifact),
                        review_report_hash=artifact["report_hash"],
                        draft_revision=artifact["draft_revision"],
                        round_number=max(1, resume_state.review_round),
                    )
                try:
                    written = writer_flow.run(WriteRequest(
                        project, chapter, request.instruction, request.title,
                        request.target_chars, request.characters, request.world,
                        "resume", stream=request.stream,
                        revision_feedback=resume_feedback))
                except KeyboardInterrupt:
                    self._save_phase("WRITER_INTERRUPTED",
                                     writer_mode=resume_state.writer_mode)
                    raise
                except Exception as exc:
                    return self._stale_result(
                        exc, chapter, rounds=rounds, chief=chief_usages,
                        writer=writer_usages, reviewer=reviewer_usages, warnings=warnings)
                chief_usages.extend(written.chief_usages)
                writer_usages.extend(written.writer_usages)
                warnings.extend(getattr(written, "warnings", []))
                if written.status == "interrupted":
                    self._save_phase("WRITER_INTERRUPTED",
                                     writer_mode=resume_state.writer_mode)
                    return self._result(
                        chapter, "WRITER_INTERRUPTED", "INTERRUPTED",
                        chief=chief_usages, writer=writer_usages, warnings=warnings,
                        report_hash=resume_state.latest_report_hash,
                        verdict=resume_state.latest_verdict)
                writer_mode = "resume"
                writer_model = getattr(written.writer_result, "model", "")
                self._save_phase(
                    "WAITING_REREVIEW" if completed_reviews else "WAITING_REVIEW",
                    writer_mode="resume")

        if (not target.exists()
                and (not request.resume or (resume_state and resume_state.phase == "INITIAL_WRITE"))):
            try:
                written = writer_flow.run(WriteRequest(
                    project, chapter, request.instruction, request.title, request.target_chars,
                    request.characters, request.world, "new", stream=request.stream))
            except KeyboardInterrupt:
                self._save_phase("WRITER_INTERRUPTED", writer_mode="new")
                raise
            except Exception as exc:
                return self._stale_result(exc, chapter, rounds=rounds, chief=chief_usages,
                                          writer=writer_usages, reviewer=reviewer_usages,
                                          warnings=warnings)
            chief_usages.extend(written.chief_usages); writer_usages.extend(written.writer_usages)
            warnings.extend(getattr(written, "warnings", []))
            if written.status == "interrupted":
                self._save_phase("WRITER_INTERRUPTED", writer_mode="new")
                return self._result(chapter, "WRITER_INTERRUPTED", "INTERRUPTED",
                                    chief=chief_usages, writer=writer_usages, warnings=warnings)
            writer_mode, writer_model = "new", getattr(written.writer_result, "model", "")
            self._save_phase("WAITING_REVIEW", writer_mode="new")
        elif target.exists() and resume_state is None:
            self._save_phase("WAITING_REVIEW", writer_mode="")

        # A current NEEDS_WORK artifact may be consumed without spending another review call.
        pending = None
        try:
            artifact = load_review_artifact(project, chapter)
            if artifact["draft_revision"] == file_revision(target) and artifact["verdict"] == "NEEDS_WORK":
                pending = (_report_from_artifact(artifact),
                           artifact["report_hash"], artifact["draft_revision"])
        except ReviewError:
            pass

        if pending is not None and not force_review:
            report, report_hash, draft_revision = pending
            previous_fingerprints = major_blocker_fingerprints(report)
            existing_preflight = _existing_review_preflight(
                review_flow,
                ReviewWorkflowRequest(project, chapter, request.review_instruction,
                                      request.characters, request.world),
            )
            codes = non_rewriteable_preflight_codes(existing_preflight)
            if codes:
                reason = ("REVIEW_CONTEXT_INSUFFICIENT"
                          if set(codes) & {"DRAFT_TRUNCATED_FOR_REVIEW", "REVIEW_CONTEXT_IMPOSSIBLE",
                                         "REVIEW_DRAFT_CONTEXT_EXHAUSTED"}
                          else "NON_REWRITEABLE_BLOCKER")
                return self._result(chapter, "ESCALATED", "ESCALATED",
                                    chief=chief_usages, writer=writer_usages,
                                    reviewer=reviewer_usages, warnings=warnings,
                                    reason=reason, report_hash=report_hash,
                                    verdict="NEEDS_WORK")
            if resume_state is not None and completed_reviews >= max_rounds:
                return self._result(chapter, "ESCALATED", "ESCALATED",
                                    chief=chief_usages, writer=writer_usages,
                                    reviewer=reviewer_usages, warnings=warnings,
                                    reason="MAX_REVIEW_ROUNDS", report_hash=report_hash,
                                    verdict="NEEDS_WORK")
            if not is_rewriteable_review(report, existing_preflight):
                return self._result(chapter, "ESCALATED", "ESCALATED",
                                    chief=chief_usages, writer=writer_usages,
                                    reviewer=reviewer_usages, warnings=warnings,
                                    reason="NON_REWRITEABLE_BLOCKER", report_hash=report_hash,
                                    verdict="NEEDS_WORK")
            feedback = build_revision_feedback(
                report, review_report_hash=report_hash, draft_revision=draft_revision,
                round_number=max(1, completed_reviews))
            before_body = _body_hash(project, chapter)
            self._save_phase(
                "WAITING_REWRITE", review_round=completed_reviews,
                latest_report_hash=report_hash, latest_verdict="NEEDS_WORK",
                issue_fingerprints=previous_fingerprints, writer_mode="rewrite")
            try:
                written = writer_flow.run(WriteRequest(
                    project, chapter, request.instruction, request.title, request.target_chars,
                    request.characters, request.world, "rewrite", stream=request.stream,
                    revision_feedback=feedback))
            except KeyboardInterrupt:
                self._save_phase("WRITER_INTERRUPTED", writer_mode="rewrite")
                raise
            except Exception as exc:
                return self._stale_result(exc, chapter, rounds=rounds, chief=chief_usages,
                                          writer=writer_usages, reviewer=reviewer_usages,
                                          warnings=warnings)
            chief_usages.extend(written.chief_usages); writer_usages.extend(written.writer_usages)
            warnings.extend(getattr(written, "warnings", []))
            if written.status == "interrupted":
                self._save_phase("WRITER_INTERRUPTED", writer_mode="rewrite")
                return self._result(chapter, "WRITER_INTERRUPTED", "INTERRUPTED",
                                    chief=chief_usages, writer=writer_usages, warnings=warnings,
                                    report_hash=report_hash, verdict="NEEDS_WORK")
            if _body_hash(project, chapter) == before_body:
                return self._result(chapter, "ESCALATED", "ESCALATED",
                                    chief=chief_usages, writer=writer_usages, warnings=warnings,
                                    reason="WRITER_NO_EFFECT", report_hash=report_hash,
                                    verdict="NEEDS_WORK")
            writer_mode, writer_model = "rewrite", getattr(written.writer_result, "model", "")
            self._save_phase("WAITING_REREVIEW", writer_mode="rewrite")
            pending = None

        for review_round in range(completed_reviews + 1, max_rounds + 1):
            round_started = _now()
            self._save_phase(
                "WAITING_REVIEW" if review_round == 1 else "WAITING_REREVIEW",
                review_round=review_round - 1)
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
            completed_reviews = review_round
            self._completed_reviews = completed_reviews
            current_fingerprints = major_blocker_fingerprints(report)
            self._save_phase(
                "WAITING_REWRITE", review_round=review_round,
                latest_report_hash=report_hash, latest_verdict=report.verdict,
                issue_fingerprints=current_fingerprints)

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
            fingerprints = current_fingerprints
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
            except KeyboardInterrupt:
                self._save_phase("WRITER_INTERRUPTED", writer_mode="rewrite")
                raise
            except Exception as exc:
                return self._stale_result(exc, chapter, rounds=rounds, chief=chief_usages,
                                          writer=writer_usages, reviewer=reviewer_usages,
                                          warnings=warnings)
            chief_usages.extend(written.chief_usages); writer_usages.extend(written.writer_usages)
            warnings.extend(getattr(written, "warnings", []))
            if written.status == "interrupted":
                self._save_phase("WRITER_INTERRUPTED", writer_mode="rewrite")
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
            self._save_phase("WAITING_REREVIEW", writer_mode="rewrite")
            if rounds:
                rounds[-1] = dataclasses.replace(rounds[-1],
                    draft_revision_before=before_revision,
                    draft_revision_after=file_revision(target))

        raise AssertionError("bounded review loop exhausted unexpectedly")
