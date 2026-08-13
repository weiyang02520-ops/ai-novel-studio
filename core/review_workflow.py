"""M6 orchestration for bounded, fail-closed, revision-safe draft review."""
from __future__ import annotations

import dataclasses
import json
from typing import Callable

from agents.review_report import ReviewIssue, ReviewLocation, ReviewReport, parse_review_report
from agents.reviewer import ReviewRequest, ReviewerError, ReviewerResult, ReviewerRunner
from llm.provider import BaseProvider, CONTEXT_TOO_LONG, ProviderError
from llm.types import Usage

from .chapter import draft_path, parse_frontmatter
from .review import ReviewResult, ReviewRun, ReviewService
from .review_context import ReviewContextBuilder, ReviewContextPlan
from .context_budget import render_review_context
from .review_preflight import PreflightIssue, ReviewPreflight, ReviewPreflightResult, merge_preflight_issues


class ReviewWorkflowError(Exception):
    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")


@dataclasses.dataclass
class ReviewWorkflowRequest:
    project: object
    chapter: int | None = None
    instruction: str = ""
    characters: list[str] = dataclasses.field(default_factory=list)
    world: list[str] = dataclasses.field(default_factory=list)
    plan_only: bool = False


@dataclasses.dataclass
class ReviewWorkflowResult:
    status: str
    chapter: int
    context_plan: ReviewContextPlan
    preflight: ReviewPreflightResult
    report: ReviewReport | None = None
    reviewer_result: ReviewerResult | None = None
    review_result: ReviewResult | None = None
    context_retried: bool = False
    usages: list[Usage] = dataclasses.field(default_factory=list)


def _preflight_issue(issue: PreflightIssue, chapter: int) -> ReviewIssue:
    severity = "BLOCKER" if issue.severity == "BLOCKER" else "INFO"
    return ReviewIssue(
        id=issue.code,
        category="OTHER",
        severity=severity,
        title=issue.code.replace("_", " ").title(),
        description=issue.message or issue.code,
        location=ReviewLocation(None, None, issue.source or None),
        evidence=(issue.source or issue.message)[:300],
        suggestion="Resolve this deterministic preflight finding before accepting the chapter.",
    )


def _merge_report(report: ReviewReport, preflight: tuple[PreflightIssue, ...], *,
                  chapter: int, draft_line_count: int) -> ReviewReport:
    deterministic = [_preflight_issue(issue, chapter) for issue in preflight]
    merged = merge_preflight_issues(deterministic, report.issues)
    payload = report.to_dict()
    payload["issues"] = [dataclasses.asdict(issue) for issue in merged]
    # Reuse the strict parser so deterministic blockers, ordering, de-duplication,
    # and PASS consistency have one implementation.
    return parse_review_report(
        json.dumps(payload, ensure_ascii=False, allow_nan=False),
        draft_line_count=draft_line_count,
    ).report


class ReviewWorkflow:
    def __init__(self, *, reviewer_provider: BaseProvider, reviewer_prompt: str, settings,
                 context_builder: ReviewContextBuilder | None = None,
                 preflight: ReviewPreflight | None = None,
                 service_factory: Callable[[object], ReviewService] = ReviewService):
        self.provider = reviewer_provider
        self.prompt = reviewer_prompt
        self.settings = settings
        context = getattr(settings, "context", {}) or {}
        self.context_builder = context_builder or ReviewContextBuilder(
            reviewer_provider,
            reviewer_prompt,
            reserve_output_tokens=int(context.get("review_reserve_output_tokens", 4096)),
            max_recent_chapters=int(context.get("max_recent_chapters", 5)),
            max_recent_text_chars=int(context.get("max_recent_text_chars", 3000)),
        )
        self.preflight = preflight or ReviewPreflight()
        self.service_factory = service_factory
        self.runner = ReviewerRunner(reviewer_provider, reviewer_prompt)

    def _begin_current(self, service: ReviewService, *, chapter: int,
                       plan: ReviewContextPlan) -> ReviewRun:
        run = service.begin(
            chapter=chapter,
            reviewer_model=getattr(self.provider.config, "model", ""),
            context_hash=plan.context_hash,
        )
        if run.draft_revision != plan.draft_revision:
            service.abort(run)
            raise ReviewWorkflowError(
                "STALE_REVIEW_DRAFT", "draft changed after review context was built")
        return run

    def run(self, request: ReviewWorkflowRequest, *,
            on_stage: Callable[[str], None] | None = None) -> ReviewWorkflowResult:
        def stage(name: str) -> None:
            if on_stage:
                on_stage(name)

        project = request.project
        chapter = request.chapter or project.current_chapter + 1
        if not isinstance(chapter, int) or isinstance(chapter, bool) or chapter < 1:
            raise ReviewWorkflowError("INVALID_REVIEW_CHAPTER", "chapter must be a positive integer")

        stage("Preflight")
        preflight = self.preflight.run(project, chapter)
        if not preflight.can_review:
            codes = ", ".join(x.code for x in preflight.blockers)
            raise ReviewWorkflowError("REVIEW_PREFLIGHT_FAILED", codes or "draft is not reviewable")

        stage("Context")
        plan = self.context_builder.build(
            project,
            chapter,
            instruction=request.instruction,
            characters=request.characters,
            world=request.world,
            draft_revision=preflight.draft_revision,
        )
        # Context-derived limitations (especially a truncated draft) are part of
        # deterministic preflight and must be evaluated before verdict merging.
        preflight = self.preflight.run(project, chapter, context_plan=plan)
        if request.plan_only:
            return ReviewWorkflowResult("planned", chapter, plan, preflight)

        service = self.service_factory(project)
        run: ReviewRun | None = None
        try:
            run = self._begin_current(service, chapter=chapter, plan=plan)

            stage("Reviewing")
            retried = False
            try:
                reviewer = self.runner.run(
                    ReviewRequest(project, chapter, run.draft_revision, plan,
                                  request.instruction, _draft_line_count(project, chapter)),
                    rendered_context=render_review_context(plan.shared),
                )
            except ProviderError as exc:
                if exc.code != CONTEXT_TOO_LONG:
                    raise
                service.abort(run)
                plan = plan.shrink(0.65)
                retried = True
                preflight = self.preflight.run(project, chapter, context_plan=plan)
                if not preflight.can_review:
                    codes = ", ".join(x.code for x in preflight.blockers)
                    raise ReviewWorkflowError("REVIEW_PREFLIGHT_FAILED", codes)
                run = self._begin_current(service, chapter=chapter, plan=plan)
                reviewer = self.runner.run(
                    ReviewRequest(project, chapter, run.draft_revision, plan,
                                  request.instruction, _draft_line_count(project, chapter)),
                    rendered_context=render_review_context(plan.shared),
                )
            report = _merge_report(
                reviewer.report,
                preflight.issues,
                chapter=chapter,
                draft_line_count=_draft_line_count(project, chapter),
            )
            stage("Saving")
            persisted = service.finalize(run, report)
            run = None  # finalize always releases the active guard
            usages = list(reviewer.usages)
            return ReviewWorkflowResult(
                "reviewed", chapter, plan, preflight, report, reviewer, persisted,
                retried, usages,
            )
        except ReviewerError as exc:
            raise ReviewWorkflowError(exc.code, exc.message) from exc
        finally:
            if run is not None:
                service.abort(run)


def _draft_line_count(project, chapter: int) -> int:
    _, body = parse_frontmatter(draft_path(project, chapter).read_text(encoding="utf-8"))
    return len(body.splitlines())
