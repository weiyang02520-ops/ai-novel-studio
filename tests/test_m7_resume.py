from __future__ import annotations

import dataclasses
import hashlib
import json
from pathlib import Path

import pytest

from core.chapter import build_frontmatter, draft_path
from core.compose_state import (
    COMPOSE_FINAL_STATES,
    COMPOSE_PHASES,
    ComposeRunState,
    ComposeRunStore,
    ComposeStateError,
    compose_status,
)
from core.creation_workflow import CreationRequest, CreationWorkflow
from core.generation import GenerationWorkspace
from core.mutation import file_revision
from core.project import create_project
from core.review import ReviewService, _report_payload_hash, report_path
from core.storage import ProjectStore, atomic_write_json, atomic_write_text
from tests.test_m7_workflow import FakeReview, FakeWrite, issue, report


def _project(tmp_path: Path):
    return create_project(ProjectStore(tmp_path / "novels"), "M7", project_id="m7-resume")


def _run(chapter: int = 1, **changes) -> ComposeRunState:
    values = {
        "chapter": chapter,
        "run_id": "a" * 32,
        "phase": "WAITING_REVIEW",
        "max_rounds": 3,
        "review_round": 0,
        "draft_revision": "ABSENT",
        "latest_report_hash": "",
        "latest_verdict": "",
        "issue_fingerprints": [],
        "started_at": "2026-08-13T00:00:00Z",
        "updated_at": "2026-08-13T00:00:00Z",
        "writer_mode": "new",
        "models": {"chief": "chief-model", "writer": "writer-model", "reviewer": "review-model"},
        "initial_instruction_hash": "b" * 64,
    }
    values.update(changes)
    return ComposeRunState(**values)


def _ai_draft(project, *, status: str = "draft", body: str = "正文\n") -> Path:
    path = draft_path(project, 1)
    meta = {
        "chapter": 1, "volume": 1, "title": "第一章", "status": status,
        "origin": "ai", "words": 2, "created_at": "2026-08-13T00:00:00Z",
        "updated_at": "2026-08-13T00:00:00Z", "characters": [],
        "generation_state": "complete", "generation_mode": "new",
        "generation_model": "writer-model", "context_hash": "c" * 64,
        "task_hash": "d" * 64,
    }
    atomic_write_text(path, build_frontmatter(meta) + body)
    return path


def test_run_state_round_trip_uses_exact_privacy_allowlist(tmp_path):
    project = _project(tmp_path)
    store = ComposeRunStore(project, 1)
    state = _run()

    store.save(state)

    assert store.load() == state
    raw = json.loads(store.path.read_text(encoding="utf-8"))
    assert set(raw) == {field.name for field in dataclasses.fields(ComposeRunState)}
    assert "instruction" not in raw
    assert "prompt" not in raw
    assert "context" not in raw
    assert "body" not in raw
    assert "evidence" not in raw


@pytest.mark.parametrize("phase", sorted(COMPOSE_PHASES))
def test_every_fixed_phase_is_valid(phase, tmp_path):
    store = ComposeRunStore(_project(tmp_path), 1)
    store.save(_run(phase=phase))
    assert store.require().phase == phase


def test_final_states_are_fixed_and_represented_by_phase():
    assert COMPOSE_FINAL_STATES == frozenset({"READY", "ESCALATED", "INTERRUPTED", "BLOCKED"})
    assert COMPOSE_FINAL_STATES <= COMPOSE_PHASES
    assert "final_state" not in {field.name for field in dataclasses.fields(ComposeRunState)}
    assert "reason" not in {field.name for field in dataclasses.fields(ComposeRunState)}


@pytest.mark.parametrize("rounds", [0, 11, True, "3"])
def test_max_rounds_is_strictly_one_to_ten(rounds):
    with pytest.raises(ComposeStateError) as exc:
        _run(max_rounds=rounds)
    assert exc.value.code == "INVALID_COMPOSE_RUN"


def test_unknown_persisted_field_is_rejected(tmp_path):
    store = ComposeRunStore(_project(tmp_path), 1)
    payload = dataclasses.asdict(_run()) | {"instruction": "USER_PRIVATE_123"}
    atomic_write_json(store.path, payload)

    with pytest.raises(ComposeStateError) as exc:
        store.load()
    assert exc.value.code == "INVALID_COMPOSE_RUN"


@pytest.mark.parametrize("raw", [b"{", b'\xff'])
def test_corrupt_or_non_utf8_run_json_is_fail_closed(tmp_path, raw):
    store = ComposeRunStore(_project(tmp_path), 1)
    store.path.parent.mkdir(parents=True, exist_ok=True)
    store.path.write_bytes(raw)

    with pytest.raises(ComposeStateError) as exc:
        store.load()
    assert exc.value.code == "INVALID_COMPOSE_RUN_JSON"


def test_run_chapter_mismatch_is_rejected(tmp_path):
    store = ComposeRunStore(_project(tmp_path), 1)
    atomic_write_json(store.path, dataclasses.asdict(_run(chapter=2)))

    with pytest.raises(ComposeStateError) as exc:
        store.load()
    assert exc.value.code == "COMPOSE_RUN_CHAPTER_MISMATCH"


def test_missing_required_run_has_explicit_code(tmp_path):
    store = ComposeRunStore(_project(tmp_path), 1)
    assert store.load() is None
    with pytest.raises(ComposeStateError) as exc:
        store.require()
    assert exc.value.code == "COMPOSE_RUN_NOT_FOUND"


def test_run_path_rejects_symlink_escape(tmp_path):
    project = _project(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    workflow = project.dir / "workflow"
    try:
        workflow.symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable")

    with pytest.raises(Exception):
        ComposeRunStore(project, 1)


def test_reset_deletes_only_compose_sidecar(tmp_path):
    project = _project(tmp_path)
    store = ComposeRunStore(project, 1)
    store.save(_run())
    draft = _ai_draft(project)
    report = project.store.safe_path(project.id, "review/ch0001.review.json")
    partial = project.store.safe_path(project.id, "drafts/.generation/ch0001.partial.md")
    history = project.store.safe_path(project.id, ".history/index.jsonl")
    for path, content in ((report, "review"), (partial, "partial"), (history, "history")):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    assert store.reset() is True
    assert not store.path.exists()
    assert draft.exists() and report.exists() and partial.exists() and history.exists()
    assert store.reset() is False


def test_compose_status_derives_no_draft_and_current_ai_draft(tmp_path):
    project = _project(tmp_path)
    empty = compose_status(project, 1)
    assert empty.chapter_state == "NO_DRAFT"
    assert empty.can_resume is False
    assert empty.can_confirm is False

    path = _ai_draft(project)
    status = compose_status(project, 1)
    assert status.chapter_state == "DRAFT"
    assert status.draft_revision == file_revision(path)
    assert status.review_current is False
    assert status.can_confirm is False


def test_compose_status_marks_partial_resumable_only_for_interrupted_writer(tmp_path):
    project = _project(tmp_path)
    store = ComposeRunStore(project, 1)
    store.save(_run(phase="WRITER_INTERRUPTED"))
    partial = project.store.safe_path(project.id, "drafts/.generation/ch0001.partial.md")
    sidecar = project.store.safe_path(project.id, "drafts/.generation/ch0001.partial.json")
    partial.parent.mkdir(parents=True, exist_ok=True)
    partial.write_text("partial", encoding="utf-8")
    atomic_write_json(sidecar, {"chapter": 1})

    status = compose_status(project, 1)
    assert status.partial_exists is True
    assert status.compose_phase == "WRITER_INTERRUPTED"
    assert status.can_resume is True


@pytest.mark.parametrize(
    ("severity", "expected"),
    [("MAJOR", False), ("BLOCKER", False), ("INFO", True)],
)
def test_compose_status_uses_the_real_pass_gate_for_can_confirm(tmp_path, severity, expected):
    project = _project(tmp_path)
    path = _ai_draft(project, status="ready")
    artifact = {
        "chapter": 1,
        "verdict": "PASS",
        "summary": "summary",
        "issues": [{
            "id": "issue-1", "category": "DIALOGUE", "severity": severity,
            "title": "title", "description": "description",
            "location": {"line_start": None, "line_end": None, "anchor": None},
            "evidence": "", "suggestion": "suggestion",
        }],
        "strengths": [],
        "task_fulfillment": "ok",
        "continuity_assessment": "ok",
        "style_assessment": "ok",
        "logic_assessment": "ok",
        "confidence": 1.0,
        "source": "llm",
        "draft_revision": file_revision(path),
        "reviewed_at": "2026-08-13T00:00:00Z",
        "reviewer_model": "review-model",
        "context_hash": "e" * 64,
    }
    artifact["report_hash"] = _report_payload_hash(artifact)
    atomic_write_json(report_path(project, 1), artifact)

    status = compose_status(project, 1)

    assert status.review_current is True
    assert status.latest_verdict == "PASS"
    assert status.can_confirm is expected


def _flows(project, writes, reviews, *, interrupted_at=None):
    writer = FakeWrite(project, writes, interrupted_at)
    reviewer = FakeReview(project, reviews)
    flow = CreationWorkflow(
        write_workflow_factory=lambda _project: writer,
        review_workflow_factory=lambda _project: reviewer,
    )
    return flow, writer, reviewer


def _prepare_partial(project, *, mode: str, base_revision: str):
    workspace = GenerationWorkspace(project, 1)
    workspace.prepare({
        "mode": mode, "title": "One", "base_revision": base_revision,
        "task_hash": "d" * 64, "context_hash": "c" * 64, "model": "writer",
        "resume_card": {},
    })
    workspace.append("partial prose")


def test_initial_writer_interrupt_persists_private_resumable_phase(tmp_path):
    project = _project(tmp_path)
    flow, _, reviewer = _flows(project, [], [], interrupted_at=1)

    result = flow.run(CreationRequest(project, instruction="USER_PRIVATE_123"))

    state = ComposeRunStore(project, 1).require()
    raw = ComposeRunStore(project, 1).path.read_text(encoding="utf-8")
    assert result.final_state == "INTERRUPTED" and state.phase == "WRITER_INTERRUPTED"
    assert state.writer_mode == "new" and state.draft_revision == "ABSENT"
    assert "USER_PRIVATE_123" not in raw and not reviewer.requests


def test_resume_interrupted_initial_writer_finishes_review_and_cleans_run(tmp_path):
    project = _project(tmp_path)
    flow, writer, reviewer = _flows(project, [], [], interrupted_at=1)
    flow.run(CreationRequest(project, instruction="same"))
    _prepare_partial(project, mode="new", base_revision="ABSENT")
    writer.interrupted_at = None
    writer.bodies.append("finished")
    reviewer.reports.append(report())
    reviewer.blockers.append(())

    result = flow.run(CreationRequest(project, resume=True))

    assert result.final_state == "READY"
    assert [request.mode for request in writer.requests] == ["new", "resume"]
    assert len(reviewer.requests) == 1
    assert not ComposeRunStore(project, 1).exists()


def test_resume_interrupted_rewrite_rebuilds_private_feedback_then_reviews_pass(tmp_path):
    project = _project(tmp_path)
    path = _ai_draft(project, body="A")
    service = ReviewService(project)
    run = service.begin(chapter=1, reviewer_model="reviewer", context_hash="x" * 64)
    persisted = service.finalize(run, report("NEEDS_WORK", [issue("fix me")]))
    flow, writer, reviewer = _flows(project, [], [], interrupted_at=1)

    interrupted = flow.run(CreationRequest(project, instruction="same"))
    assert interrupted.final_state == "INTERRUPTED"
    state = ComposeRunStore(project, 1).require()
    assert state.writer_mode == "rewrite" and state.latest_report_hash == persisted.report_hash
    _prepare_partial(project, mode="rewrite", base_revision=file_revision(path))
    writer.interrupted_at = None
    writer.bodies.append("B")
    reviewer.reports.append(report())
    reviewer.blockers.append(())

    result = flow.run(CreationRequest(project, instruction="same", resume=True))

    assert result.final_state == "READY"
    assert writer.requests[-1].mode == "resume"
    feedback = writer.requests[-1].revision_feedback
    assert feedback is not None and feedback.review_report_hash == persisted.report_hash
    assert feedback.draft_revision == state.draft_revision
    assert len(reviewer.requests) == 1
    assert not ComposeRunStore(project, 1).exists()


def test_compose_sidecar_never_contains_feedback_or_partial_sentinels(tmp_path):
    project = _project(tmp_path)
    path = _ai_draft(project, body="NOVEL_PRIVATE_456")
    service = ReviewService(project)
    run = service.begin(chapter=1, reviewer_model="reviewer", context_hash="x" * 64)
    service.finalize(run, report("NEEDS_WORK", [issue("REVIEW_PRIVATE_789")]))
    flow, _, _ = _flows(project, [], [], interrupted_at=1)
    flow.run(CreationRequest(project, instruction="USER_PRIVATE_123"))

    raw = ComposeRunStore(project, 1).path.read_text(encoding="utf-8")
    assert "USER_PRIVATE_123" not in raw
    assert "NOVEL_PRIVATE_456" not in raw
    assert "REVIEW_PRIVATE_789" not in raw


@pytest.mark.parametrize("phase", ["WAITING_REVIEW", "WAITING_REREVIEW"])
def test_resume_waiting_review_phases_do_not_repeat_writer(tmp_path, phase):
    project = _project(tmp_path)
    path = _ai_draft(project, body="A")
    flow, writer, reviewer = _flows(project, [], [report()])
    now = "2026-08-13T00:00:00Z"
    ComposeRunStore(project, 1).save(_run(
        phase=phase, draft_revision=file_revision(path), writer_mode="rewrite" if
        phase == "WAITING_REREVIEW" else "new",
        review_round=1 if phase == "WAITING_REREVIEW" else 0,
        models={"chief": "", "writer": "", "reviewer": ""},
        initial_instruction_hash=hashlib.sha256(b"same").hexdigest(),
        started_at=now, updated_at=now,
    ))

    result = flow.run(CreationRequest(project, instruction="same", resume=True))

    assert result.final_state == "READY"
    assert not writer.requests and len(reviewer.requests) == 1
    assert result.rounds_completed == 1 + (1 if phase == "WAITING_REREVIEW" else 0)


@pytest.mark.parametrize(
    ("request_change", "reason"),
    [({"instruction": "different"}, "COMPOSE_INSTRUCTION_MISMATCH"),
     ({"instruction": "same", "max_review_rounds": 2}, "COMPOSE_MAX_ROUNDS_MISMATCH")],
)
def test_resume_configuration_mismatch_blocks_without_touching_partial(
        tmp_path, request_change, reason):
    project = _project(tmp_path)
    flow, writer, _ = _flows(project, [], [], interrupted_at=1)
    flow.run(CreationRequest(project, instruction="same", max_review_rounds=3))
    _prepare_partial(project, mode="new", base_revision="ABSENT")
    before = GenerationWorkspace(project, 1).text()

    result = flow.run(CreationRequest(project, resume=True, **request_change))

    assert result.final_state == "BLOCKED" and result.reason == reason
    assert GenerationWorkspace(project, 1).text() == before
    assert len(writer.requests) == 1
    assert ComposeRunStore(project, 1).require().phase == "WRITER_INTERRUPTED"


def test_resume_after_mismatched_instruction_can_retry_with_empty_instruction(tmp_path):
    project = _project(tmp_path)
    flow, writer, reviewer = _flows(project, [], [], interrupted_at=1)
    flow.run(CreationRequest(project, instruction="private original"))
    _prepare_partial(project, mode="new", base_revision="ABSENT")

    mismatch = flow.run(CreationRequest(project, instruction="wrong", resume=True))
    assert mismatch.reason == "COMPOSE_INSTRUCTION_MISMATCH"
    assert ComposeRunStore(project, 1).require().phase == "WRITER_INTERRUPTED"

    writer.interrupted_at = None
    writer.bodies.append("finished")
    reviewer.reports.append(report())
    reviewer.blockers.append(())
    resumed = flow.run(CreationRequest(project, resume=True))
    assert resumed.final_state == "READY"


def test_resume_rejects_partial_base_revision_mismatch(tmp_path):
    project = _project(tmp_path)
    flow, writer, _ = _flows(project, [], [], interrupted_at=1)
    flow.run(CreationRequest(project, instruction="same"))
    _prepare_partial(project, mode="new", base_revision="a" * 64)

    result = flow.run(CreationRequest(project, instruction="same", resume=True))

    assert result.final_state == "BLOCKED"
    assert result.reason == "STALE_WORKFLOW_STATE"
    assert len(writer.requests) == 1


def test_resume_rejects_model_configuration_mismatch(tmp_path):
    project = _project(tmp_path)
    path = _ai_draft(project)
    flow, writer, _ = _flows(project, [], [report()])
    ComposeRunStore(project, 1).save(_run(
        phase="WAITING_REVIEW", draft_revision=file_revision(path),
        models={"chief": "old", "writer": "", "reviewer": ""},
        initial_instruction_hash=hashlib.sha256(b"same").hexdigest(),
    ))

    result = flow.run(CreationRequest(project, instruction="same", resume=True))

    assert result.final_state == "BLOCKED"
    assert result.reason == "COMPOSE_MODEL_CONFIG_MISMATCH"
    assert not writer.requests


def test_resume_waiting_rewrite_rejects_report_hash_mismatch(tmp_path):
    project = _project(tmp_path)
    path = _ai_draft(project)
    service = ReviewService(project)
    run = service.begin(chapter=1, reviewer_model="reviewer", context_hash="x" * 64)
    persisted = service.finalize(run, report("NEEDS_WORK", [issue()]))
    flow, writer, _ = _flows(project, [], [])
    ComposeRunStore(project, 1).save(_run(
        phase="WAITING_REWRITE", draft_revision=file_revision(path), review_round=1,
        writer_mode="rewrite", latest_report_hash="f" * 64,
        latest_verdict="NEEDS_WORK", models={"chief": "", "writer": "", "reviewer": ""},
        initial_instruction_hash=hashlib.sha256(b"same").hexdigest(),
    ))
    assert persisted.report_hash != "f" * 64

    result = flow.run(CreationRequest(project, instruction="same", resume=True))

    assert result.final_state == "BLOCKED" and result.reason == "COMPOSE_REPORT_MISMATCH"
    assert not writer.requests


def test_review_interrupt_leaves_waiting_review_for_next_resume(tmp_path):
    project = _project(tmp_path)
    writer = FakeWrite(project, ["draft"])

    class InterruptReview:
        provider = type("P", (), {"config": type("C", (), {"model": "reviewer"})()})()
        def run(self, *_args, **_kwargs):
            raise KeyboardInterrupt

    flow = CreationWorkflow(write_workflow_factory=lambda _p: writer,
                            review_workflow_factory=lambda _p: InterruptReview())
    with pytest.raises(KeyboardInterrupt):
        flow.run(CreationRequest(project, instruction="same"))
    state = ComposeRunStore(project, 1).require()
    assert state.phase == "WAITING_REVIEW"
    assert state.draft_revision == file_revision(draft_path(project, 1))


def test_writer_keyboard_interrupt_records_interrupted_phase(tmp_path):
    project = _project(tmp_path)

    class InterruptWriter:
        def run(self, *_args, **_kwargs):
            raise KeyboardInterrupt

    flow = CreationWorkflow(write_workflow_factory=lambda _p: InterruptWriter(),
                            review_workflow_factory=lambda _p: FakeReview(project, []))
    with pytest.raises(KeyboardInterrupt):
        flow.run(CreationRequest(project, instruction="same"))

    state = ComposeRunStore(project, 1).require()
    assert state.phase == "WRITER_INTERRUPTED" and state.writer_mode == "new"


def test_escalated_run_is_retained_but_ready_run_is_deleted(tmp_path):
    project = _project(tmp_path / "escalated")
    flow, _, _ = _flows(project, ["A"], [report("NEEDS_WORK", [])])
    result = flow.run(CreationRequest(project, max_review_rounds=1))
    assert result.final_state == "ESCALATED"
    assert ComposeRunStore(project, 1).require().phase == "ESCALATED"

    project = _project(tmp_path / "ready")
    flow, _, _ = _flows(project, ["A"], [report()])
    assert flow.run(CreationRequest(project)).final_state == "READY"
    assert not ComposeRunStore(project, 1).exists()


def test_escalated_run_can_resume_after_project_fix(tmp_path):
    project = _project(tmp_path)
    path = _ai_draft(project, body="A")
    service = ReviewService(project)
    run = service.begin(chapter=1, reviewer_model="reviewer", context_hash="x" * 64)
    persisted = service.finalize(run, report("NEEDS_WORK", [issue()]))
    ComposeRunStore(project, 1).save(_run(
        phase="ESCALATED", draft_revision=file_revision(path), review_round=1,
        latest_report_hash=persisted.report_hash, latest_verdict="NEEDS_WORK",
        issue_fingerprints=(), writer_mode="rewrite",
        models={"chief": "", "writer": "", "reviewer": ""},
        initial_instruction_hash=hashlib.sha256(b"private").hexdigest(),
    ))
    flow, writer, reviewer = _flows(project, [], [report()])

    result = flow.run(CreationRequest(project, resume=True))

    assert result.final_state == "READY"
    assert not writer.requests and len(reviewer.requests) == 1


def test_resume_reconciles_canonical_draft_committed_before_initial_phase_save(tmp_path):
    project = _project(tmp_path)
    path = _ai_draft(project, body="committed")
    ComposeRunStore(project, 1).save(_run(
        phase="INITIAL_WRITE", draft_revision="ABSENT", review_round=0,
        writer_mode="new", models={"chief": "", "writer": "", "reviewer": ""},
        initial_instruction_hash=hashlib.sha256(b"private").hexdigest(),
    ))
    flow, writer, reviewer = _flows(project, [], [report()])

    result = flow.run(CreationRequest(project, resume=True))

    assert result.final_state == "READY"
    assert file_revision(path) == result.draft_revision
    assert not writer.requests and len(reviewer.requests) == 1


def test_resume_reconciles_rewrite_committed_before_interrupted_phase_save(tmp_path):
    project = _project(tmp_path)
    path = _ai_draft(project, body="A")
    service = ReviewService(project)
    run = service.begin(chapter=1, reviewer_model="reviewer", context_hash="x" * 64)
    persisted = service.finalize(run, report("NEEDS_WORK", [issue()]))
    old_revision = file_revision(path)
    _ai_draft(project, body="B")
    ComposeRunStore(project, 1).save(_run(
        phase="WRITER_INTERRUPTED", draft_revision=old_revision, review_round=1,
        latest_report_hash=persisted.report_hash, latest_verdict="NEEDS_WORK",
        writer_mode="rewrite", models={"chief": "", "writer": "", "reviewer": ""},
        initial_instruction_hash=hashlib.sha256(b"private").hexdigest(),
    ))
    flow, writer, reviewer = _flows(project, [], [report()])

    result = flow.run(CreationRequest(project, resume=True))

    assert result.final_state == "READY"
    assert not writer.requests and len(reviewer.requests) == 1
    assert result.rounds_completed == 2


def test_resume_reconciles_needs_work_report_committed_before_round_state_save(tmp_path):
    project = _project(tmp_path)
    path = _ai_draft(project, body="A")
    ComposeRunStore(project, 1).save(_run(
        phase="WAITING_REVIEW", draft_revision=file_revision(path), review_round=0,
        latest_report_hash="", latest_verdict="", writer_mode="new",
        models={"chief": "", "writer": "", "reviewer": ""},
        initial_instruction_hash=hashlib.sha256(b"private").hexdigest(),
    ))
    service = ReviewService(project)
    run = service.begin(chapter=1, reviewer_model="reviewer", context_hash="x" * 64)
    service.finalize(run, report("NEEDS_WORK", [issue()]))
    flow, writer, reviewer = _flows(project, ["B"], [report()])

    result = flow.run(CreationRequest(project, resume=True))

    assert result.final_state == "READY"
    assert len(writer.requests) == 1 and len(reviewer.requests) == 1
    assert result.rounds_completed == 2
