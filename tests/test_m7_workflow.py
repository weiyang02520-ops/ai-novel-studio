from __future__ import annotations

import dataclasses
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from agents.review_report import ReviewIssue, ReviewLocation, ReviewReport
from core.chapter import build_frontmatter, confirmed_path, draft_path, parse_frontmatter
from core.creation_workflow import CreationRequest, CreationWorkflow
from core.mutation import ABSENT, file_revision
from core.project import create_project
from core.review import ReviewService
from core.review_preflight import PreflightIssue, ReviewPreflightResult
from core.storage import ProjectStore, atomic_write_text
from llm.types import Usage


def project(tmp_path):
    return create_project(ProjectStore(tmp_path / "novels"), "M7", project_id="m7-loop")


def save_draft(p, body="A", *, origin="ai", status="draft"):
    path = draft_path(p, 1)
    meta = {"chapter": 1, "volume": 1, "title": "One", "status": status,
            "origin": origin, "words": len(body), "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-01T00:00:00Z", "characters": [],
            "generation_state": "complete", "generation_mode": "new",
            "generation_model": "writer", "context_hash": "c" * 64, "task_hash": "t" * 64}
    atomic_write_text(path, build_frontmatter(meta) + body)
    return path


def issue(title="dialogue", *, severity="MAJOR", category="DIALOGUE"):
    return ReviewIssue("i", category, severity, title, "desc",
                       ReviewLocation(1, 1, "anchor"), "evidence", "fix")


def report(verdict="PASS", issues=()):
    return ReviewReport(1, verdict, "summary", tuple(issues), ("strength",),
                        "ok", "ok", "ok", "ok", .9, "reviewer")


def preflight(*codes):
    rows = tuple(PreflightIssue("BLOCKER", code, code) for code in codes)
    return ReviewPreflightResult(1, "d" * 64, rows, True, not rows)


class FakeWrite:
    def __init__(self, p, bodies, interrupted_at=None):
        self.p, self.bodies, self.requests = p, list(bodies), []
        self.interrupted_at = interrupted_at

    def run(self, req, **_):
        self.requests.append(req)
        if self.interrupted_at == len(self.requests):
            return NS(status="interrupted", chapter=1, chief_usages=[], writer_usages=[],
                      writer_result=NS(model="writer"), draft_result=None, warnings=[])
        body = self.bodies.pop(0)
        before = file_revision(draft_path(self.p, 1))
        path = save_draft(self.p, body)
        return NS(status="saved", chapter=1,
                  chief_usages=[Usage(1, 2, 3)], writer_usages=[Usage(2, 3, 5)],
                  writer_result=NS(model="writer"),
                  draft_result=NS(revision=file_revision(path)), warnings=[], before=before)


class FakeReview:
    def __init__(self, p, reports, blockers=None):
        self.p, self.reports, self.requests = p, list(reports), []
        self.blockers = list(blockers or [()] * len(reports))

    def run(self, req, **_):
        self.requests.append(req)
        value = self.reports.pop(0)
        pf = preflight(*self.blockers.pop(0))
        path = draft_path(self.p, 1)
        run = ReviewService(self.p).begin(chapter=1, reviewer_model="reviewer", context_hash="x" * 64)
        persisted = ReviewService(self.p).finalize(run, value)
        return NS(status="reviewed", chapter=1, report=value, preflight=pf,
                  review_result=persisted, reviewer_result=NS(model="reviewer"),
                  usages=[Usage(3, 4, 7)])


def workflow(p, writes, reviews, blockers=None, interrupted_at=None):
    writer = FakeWrite(p, writes, interrupted_at)
    reviewer = FakeReview(p, reviews, blockers)
    return CreationWorkflow(write_workflow_factory=lambda _p: writer,
                            review_workflow_factory=lambda _p: reviewer), writer, reviewer


def test_entry_confirmed_and_manual_are_protected(tmp_path):
    p = project(tmp_path); confirmed_path(p, 1).parent.mkdir(exist_ok=True)
    confirmed_path(p, 1).write_text("confirmed", encoding="utf-8")
    flow, writer, reviewer = workflow(p, [], [])
    result = flow.run(CreationRequest(p, 1))
    assert (result.status, result.final_state) == ("ALREADY_CONFIRMED", "BLOCKED")
    confirmed_path(p, 1).unlink(); save_draft(p, origin="manual")
    result = flow.run(CreationRequest(p, 1))
    assert result.status == "MANUAL_DRAFT_PROTECTED"
    assert not writer.requests and not reviewer.requests


def test_no_draft_initial_pass_and_invariants(tmp_path):
    p = project(tmp_path); before = p.current_chapter
    flow, writer, reviewer = workflow(p, ["A"], [report()])
    result = flow.run(CreationRequest(p, max_review_rounds=3))
    assert result.final_state == "READY" and result.rounds_completed == 1
    assert len(writer.requests) == len(reviewer.requests) == 1
    assert len(result.chief_usages) == len(result.writer_usages) == len(result.reviewer_usages) == 1
    assert result.rounds[0].draft_revision_after == result.draft_revision
    assert result.rounds[0].started_at and result.rounds[0].finished_at
    assert p.current_chapter == before and not confirmed_path(p, 1).exists()


def test_ready_with_current_pass_does_not_call_models(tmp_path):
    p = project(tmp_path); save_draft(p)
    service = ReviewService(p); run = service.begin(chapter=1, reviewer_model="r", context_hash="x" * 64)
    service.finalize(run, report())
    flow, writer, reviewer = workflow(p, [], [])
    result = flow.run(CreationRequest(p, 1))
    assert result.final_state == "READY" and result.rounds_completed == 0
    assert not writer.requests and not reviewer.requests


@pytest.mark.parametrize("writes,reviews", [(["A", "B"], [report("NEEDS_WORK", [issue()]), report()]),
                                              (["A", "B", "C"], [report("NEEDS_WORK", [issue("one")]), report("NEEDS_WORK", [issue("two")]), report()])])
def test_one_or_two_rewrites_reach_pass(tmp_path, writes, reviews):
    p = project(tmp_path); flow, writer, reviewer = workflow(p, writes, reviews)
    result = flow.run(CreationRequest(p, max_review_rounds=3))
    assert result.final_state == "READY"
    assert result.rounds_completed == len(reviews)
    assert [x.mode for x in writer.requests] == ["new"] + ["rewrite"] * (len(writes) - 1)
    assert all(x.revision_feedback is not None for x in writer.requests[1:])


def test_existing_draft_is_reviewed_without_duplicate_initial_write(tmp_path):
    p = project(tmp_path); save_draft(p)
    flow, writer, reviewer = workflow(p, [], [report()])
    assert flow.run(CreationRequest(p, 1)).final_state == "READY"
    assert not writer.requests and len(reviewer.requests) == 1


def test_current_needs_work_is_rewritten_then_keeps_full_reviewer_budget(tmp_path):
    p = project(tmp_path); save_draft(p, "A")
    service = ReviewService(p); run = service.begin(chapter=1, reviewer_model="r", context_hash="x" * 64)
    service.finalize(run, report("NEEDS_WORK", [issue("old")]))
    flow, writer, reviewer = workflow(
        p, ["B", "C"], [report("NEEDS_WORK", [issue("new")]), report()])

    result = flow.run(CreationRequest(p, 1, max_review_rounds=2))

    assert result.final_state == "READY"
    assert len(reviewer.requests) == result.rounds_completed == 2
    assert [request.mode for request in writer.requests] == ["rewrite", "rewrite"]


def test_max_rounds_nonrewriteable_stall_progress_and_no_effect(tmp_path):
    p = project(tmp_path); flow, _, reviewer = workflow(
        p, ["A", "B", "C"], [report("NEEDS_WORK", [issue("a")])] * 3)
    result = flow.run(CreationRequest(p, max_review_rounds=3))
    assert result.final_state == "ESCALATED" and result.reason in {"STALLED_REVIEW", "MAX_REVIEW_ROUNDS"}
    assert len(reviewer.requests) <= 3

    p = project(tmp_path / "block"); flow, writer, _ = workflow(
        p, ["A"], [report("NEEDS_WORK", [issue()])], [("DRAFT_TRUNCATED_FOR_REVIEW",)])
    result = flow.run(CreationRequest(p))
    assert result.reason == "REVIEW_CONTEXT_INSUFFICIENT" and len(writer.requests) == 1

    p = project(tmp_path / "progress"); flow, _, _ = workflow(
        p, ["A", "B", "C"], [report("NEEDS_WORK", [issue("a"), issue("b")]),
                                report("NEEDS_WORK", [issue("a")]), report()])
    assert flow.run(CreationRequest(p, max_review_rounds=3)).final_state == "READY"

    p = project(tmp_path / "same"); flow, _, reviewer = workflow(
        p, ["A", "A"], [report("NEEDS_WORK", [issue()])])
    result = flow.run(CreationRequest(p))
    assert result.reason == "WRITER_NO_EFFECT" and len(reviewer.requests) == 1


def test_distinct_findings_stop_at_exact_reviewer_limit(tmp_path):
    p = project(tmp_path)
    findings = [report("NEEDS_WORK", [issue(str(number))]) for number in range(3)]
    flow, writer, reviewer = workflow(p, ["A", "B", "C"], findings)

    result = flow.run(CreationRequest(p, max_review_rounds=3))

    assert result.reason == "MAX_REVIEW_ROUNDS"
    assert result.rounds_completed == len(reviewer.requests) == 3
    assert len(writer.requests) == 3  # initial + at most max_rounds-1 rewrites


def test_interrupted_writer_returns_interrupted_without_review(tmp_path):
    p = project(tmp_path); flow, _, reviewer = workflow(p, [], [], interrupted_at=1)
    result = flow.run(CreationRequest(p))
    assert result.final_state == "INTERRUPTED" and result.status == "WRITER_INTERRUPTED"
    assert not reviewer.requests
