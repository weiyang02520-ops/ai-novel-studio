from __future__ import annotations

import dataclasses

import pytest

from core.compose_state import ComposeRunState, ComposeRunStore
from core.knowledge import doctor
from core.project import create_project
from core.storage import ProjectStore, atomic_write_json


def _project(tmp_path):
    return create_project(ProjectStore(tmp_path / "novels"), "M7 Doctor", project_id="m7-doctor")


def _state(**changes):
    values = {
        "chapter": 1, "run_id": "a" * 32, "phase": "WAITING_REVIEW",
        "max_rounds": 3, "review_round": 0, "draft_revision": "ABSENT",
        "latest_report_hash": "", "latest_verdict": "", "issue_fingerprints": [],
        "started_at": "2026-08-13T00:00:00Z", "updated_at": "2026-08-13T00:00:00Z",
        "writer_mode": "new",
        "models": {"chief": "c", "writer": "w", "reviewer": "r"},
        "initial_instruction_hash": "b" * 64,
    }
    values.update(changes)
    return ComposeRunState(**values)


def _codes(project):
    return {item["code"] for item in doctor(project)}


def test_doctor_reports_orphan_compose_run(tmp_path):
    project = _project(tmp_path)
    ComposeRunStore(project, 1).save(_state())
    assert "ORPHAN_COMPOSE_RUN" in _codes(project)


def test_doctor_reports_invalid_compose_schema(tmp_path):
    project = _project(tmp_path)
    store = ComposeRunStore(project, 1)
    atomic_write_json(store.path, dataclasses.asdict(_state()) | {"prompt": "PRIVATE"})
    assert "INVALID_COMPOSE_RUN" in _codes(project)


def test_doctor_reports_filename_payload_chapter_mismatch(tmp_path):
    project = _project(tmp_path)
    store = ComposeRunStore(project, 1)
    atomic_write_json(store.path, dataclasses.asdict(_state(chapter=2)))
    assert "COMPOSE_RUN_CHAPTER_MISMATCH" in _codes(project)


def test_doctor_reports_ready_run_without_ready_canonical_draft(tmp_path):
    project = _project(tmp_path)
    ComposeRunStore(project, 1).save(_state(phase="READY"))
    codes = _codes(project)
    assert "ORPHAN_COMPOSE_RUN" in codes
    assert "COMPOSE_READY_STATE_MISMATCH" in codes


def test_doctor_rejects_compose_run_symlink(tmp_path):
    project = _project(tmp_path)
    outside = tmp_path / "outside.json"
    outside.write_text("{}", encoding="utf-8")
    run = project.dir / "workflow/.runs/ch0001.compose.json"
    run.parent.mkdir(parents=True)
    try:
        run.symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable")
    assert "UNSAFE_COMPOSE_RUN_PATH" in _codes(project)
