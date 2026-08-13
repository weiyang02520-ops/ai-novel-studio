from __future__ import annotations

import dataclasses
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
from core.mutation import file_revision
from core.project import create_project
from core.review import _report_payload_hash, report_path
from core.storage import ProjectStore, atomic_write_json, atomic_write_text


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
