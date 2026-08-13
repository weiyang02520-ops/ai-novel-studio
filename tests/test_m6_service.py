from __future__ import annotations

import json
from dataclasses import dataclass

import pytest

from core.ai_draft import AIChapterDraftService
from core.chapter import confirm_draft, draft_path, parse_frontmatter
from core.history import undo_last
from core.mutation import ABSENT, file_revision
from core.project import create_project
from core.storage import DataIntegrityError, ProjectStore, atomic_write_text


@pytest.fixture
def project(tmp_path):
    return create_project(ProjectStore(tmp_path / "novels"), "M6", project_id="m6-service")


def ai_draft(project, body="line one\nline two\n"):
    return AIChapterDraftService(project).finalize(
        chapter=1, title="One", body=body, mode="new", generation_state="complete",
        model="writer", context_hash="c" * 64, task_hash="t" * 64,
        expected_revision=ABSENT,
    )


@dataclass(frozen=True)
class FakeReport:
    chapter: int = 1
    verdict: str = "PASS"
    issues: tuple = ()

    def to_dict(self):
        return {
            "chapter": self.chapter, "verdict": self.verdict, "summary": "ok",
            "issues": [{"id": f"i{n}", "category": x.get("category", "OTHER"),
                        "severity": x.get("severity", "INFO"), "title": x.get("title", "issue"),
                        "description": x.get("description", "detail"),
                        "location": {"line_start": None, "line_end": None, "anchor": None},
                        "evidence": x.get("evidence", ""), "suggestion": x.get("suggestion", "fix")}
                       for n, x in enumerate(self.issues)], "strengths": ["clear"],
            "task_fulfillment": "ok", "continuity_assessment": "ok",
            "style_assessment": "ok", "logic_assessment": "ok",
            "confidence": 0.9, "source": "reviewer",
        }


def service():
    from core.review import ReviewService
    return ReviewService


def test_pass_is_two_file_revision_bound_transaction_and_body_is_invariant(project):
    ai_draft(project)
    path = draft_path(project, 1)
    before_meta, before_body = parse_frontmatter(path.read_text(encoding="utf-8"))
    svc = service()(project)
    run = svc.begin(chapter=1, reviewer_model="reviewer", context_hash="x" * 64)
    result = svc.finalize(run, FakeReport())

    meta, body = parse_frontmatter(path.read_text(encoding="utf-8"))
    artifact = json.loads((project.dir / result.report_path).read_text(encoding="utf-8"))
    assert meta["status"] == "ready" and meta["origin"] == "ai"
    assert body == before_body and before_meta["origin"] == meta["origin"]
    assert artifact["draft_revision"] == file_revision(path) == result.draft_revision
    assert artifact["report_hash"] == result.report_hash
    assert project.current_chapter == 0
    assert not (project.dir / "chapters/ch0001.md").exists()


def test_needs_work_preserves_entire_draft_bytes_and_report_is_current(project):
    ai_draft(project)
    path = draft_path(project, 1)
    before = path.read_bytes()
    svc = service()(project)
    run = svc.begin(chapter=1, reviewer_model="reviewer", context_hash="x")
    result = svc.finalize(run, FakeReport(verdict="NEEDS_WORK", issues=({"severity": "MAJOR"},)))
    assert path.read_bytes() == before
    assert json.loads((project.dir / result.report_path).read_text(encoding="utf-8"))["draft_revision"] == file_revision(path)


def test_stale_draft_and_report_races_overwrite_nothing(project):
    from core.review import ReviewError
    ai_draft(project)
    svc = service()(project)
    run = svc.begin(chapter=1, reviewer_model="r", context_hash="h")
    p = draft_path(project, 1)
    atomic_write_text(p, p.read_text(encoding="utf-8") + "external")
    with pytest.raises(ReviewError, match="STALE_REVIEW_DRAFT"):
        svc.finalize(run, FakeReport())
    assert p.read_text(encoding="utf-8").endswith("external")

    # A report race is independently protected.
    svc.abort(run)
    meta, body = parse_frontmatter(p.read_text(encoding="utf-8"))
    atomic_write_text(p, __import__("core.chapter", fromlist=["build_frontmatter"]).build_frontmatter(meta) + body)
    run2 = svc.begin(chapter=1, reviewer_model="r", context_hash="h")
    rp = project.dir / "review/ch0001.review.json"
    rp.parent.mkdir(parents=True, exist_ok=True); atomic_write_text(rp, "external report")
    with pytest.raises(ReviewError, match="STALE_REVIEW_REPORT"):
        svc.finalize(run2, FakeReport())
    assert rp.read_text(encoding="utf-8") == "external report"


def test_reopen_and_all_review_operations_are_undoable(project):
    ai_draft(project)
    svc = service()(project)
    passed = svc.finalize(svc.begin(chapter=1, reviewer_model="r", context_hash="h"), FakeReport())
    assert parse_frontmatter(draft_path(project, 1).read_text(encoding="utf-8"))[0]["status"] == "ready"
    svc.reopen(chapter=1, expected_revision=passed.draft_revision)
    assert parse_frontmatter(draft_path(project, 1).read_text(encoding="utf-8"))[0]["status"] == "draft"
    undo_last(project)
    assert parse_frontmatter(draft_path(project, 1).read_text(encoding="utf-8"))[0]["status"] == "ready"
    undo_last(project)
    assert parse_frontmatter(draft_path(project, 1).read_text(encoding="utf-8"))[0]["status"] == "draft"
    assert not (project.dir / "review/ch0001.review.json").exists()


def test_confirm_ai_ready_requires_current_valid_pass_report(project):
    ai_draft(project)
    svc = service()(project)
    svc.finalize(svc.begin(chapter=1, reviewer_model="r", context_hash="h"), FakeReport())
    report = project.dir / "review/ch0001.review.json"
    report.unlink()
    with pytest.raises(DataIntegrityError, match="review"):
        confirm_draft(project, 1)


def test_recover_is_explicitly_no_persisted_pending(project):
    from core.review import ReviewError
    ai_draft(project)
    with pytest.raises(ReviewError, match="NO_PENDING_REVIEW"):
        service()(project).recover(chapter=1)


def test_transaction_failure_restores_draft_and_previous_report_bytes(project):
    from core.review import ReviewError, ReviewService
    ai_draft(project)
    path = draft_path(project, 1)
    rp = project.dir / "review/ch0001.review.json"
    rp.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(rp, "old external report")
    draft_before, report_before = path.read_bytes(), rp.read_bytes()
    calls = 0

    def fail_second(target, text):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("report write fault")
        atomic_write_text(target, text)

    svc = ReviewService(project, writer=fail_second)
    run = svc.begin(chapter=1, reviewer_model="r", context_hash="h")
    with pytest.raises(ReviewError, match="REVIEW_TRANSACTION_FAILED"):
        svc.finalize(run, FakeReport())
    assert path.read_bytes() == draft_before
    assert rp.read_bytes() == report_before


def test_rereview_overwrites_report_and_undo_restores_previous_artifact(project):
    svc = service()(project); ai_draft(project)
    first = svc.finalize(svc.begin(chapter=1, reviewer_model="r1", context_hash="h1"),
                         FakeReport(verdict="NEEDS_WORK"))
    rp = project.dir / first.report_path
    old = rp.read_bytes()
    svc.finalize(svc.begin(chapter=1, reviewer_model="r2", context_hash="h2"),
                 FakeReport(verdict="NEEDS_WORK", issues=({"severity": "MINOR"},)))
    assert rp.read_bytes() != old
    undo_last(project)
    assert rp.read_bytes() == old


def test_valid_current_pass_can_confirm_and_advances_only_at_confirm(project):
    ai_draft(project); svc = service()(project)
    svc.finalize(svc.begin(chapter=1, reviewer_model="r", context_hash="h"), FakeReport())
    assert project.current_chapter == 0
    confirmed = confirm_draft(project, 1)
    assert confirmed.status == "confirmed" and confirmed.origin == "ai"
    assert project.current_chapter == 1


def test_rollback_never_overwrites_external_bytes_written_mid_transaction(project):
    from core.review import ReviewError, ReviewService
    ai_draft(project); path = draft_path(project, 1)
    original = path.read_bytes(); external = b'external report wins'
    calls = 0

    def race_writer(target, text):
        nonlocal calls
        calls += 1
        if calls == 2:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(external)
            raise OSError("external report race")
        atomic_write_text(target, text)

    svc = ReviewService(project, writer=race_writer)
    run = svc.begin(chapter=1, reviewer_model="r", context_hash="h")
    with pytest.raises(ReviewError, match="REVIEW_ROLLBACK_FAILED"):
        svc.finalize(run, FakeReport())
    assert path.read_bytes() == original
    assert (project.dir / "review/ch0001.review.json").read_bytes() == external


def test_concurrent_begins_are_safe_first_finalize_wins(project):
    from core.review import ReviewError
    ai_draft(project); svc = service()(project)
    first = svc.begin(chapter=1, reviewer_model="r1", context_hash="h1")
    second = svc.begin(chapter=1, reviewer_model="r2", context_hash="h2")
    svc.finalize(first, FakeReport(verdict="NEEDS_WORK"))
    with pytest.raises(ReviewError, match="STALE_REVIEW_REPORT"):
        svc.finalize(second, FakeReport(verdict="NEEDS_WORK"))
