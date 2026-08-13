from __future__ import annotations

import json
import pytest

from agents.review_report import parse_review_report
from core.ai_draft import AIChapterDraftService
from core.chapter import draft_path
from core.knowledge import doctor
from core.mutation import ABSENT, file_revision
from core.project import create_project
from core.review import ReviewService, report_path
from core.storage import ProjectStore, atomic_write_text


@pytest.fixture
def project(tmp_path):
    return create_project(ProjectStore(tmp_path / "novels"), "M6 Doctor", project_id="m6-doctor")


def _pass_report():
    payload = {
        "chapter": 1, "verdict": "PASS", "summary": "ready", "issues": [],
        "strengths": ["clear"], "task_fulfillment": "complete",
        "continuity_assessment": "consistent", "style_assessment": "consistent",
        "logic_assessment": "sound", "confidence": 0.9, "source": "reviewer",
    }
    return parse_review_report(json.dumps(payload)).report


def _ready(project):
    AIChapterDraftService(project).finalize(
        chapter=1, title="One", body="line one\nline two\n", mode="new",
        generation_state="complete", model="writer", context_hash="c" * 64,
        task_hash="t" * 64, expected_revision=ABSENT,
    )
    service = ReviewService(project)
    run = service.begin(chapter=1, reviewer_model="reviewer", context_hash="x" * 64)
    return service.finalize(run, _pass_report())


def _review_codes(project):
    return {item["code"] for item in doctor(project) if "REVIEW" in item["code"]}


def test_doctor_accepts_ready_with_current_strict_pass_report(project):
    _ready(project)
    assert _review_codes(project) == set()


def test_doctor_rejects_ready_without_report(project):
    result = _ready(project)
    (project.dir / result.report_path).unlink()
    issues = doctor(project)
    assert any(x["severity"] == "ERROR" and x["code"] == "AI_READY_REVIEW_MISSING"
               for x in issues)


def test_doctor_rejects_ready_with_stale_report(project):
    result = _ready(project)
    path = project.dir / result.report_path
    data = json.loads(path.read_text(encoding="utf-8"))
    data["draft_revision"] = "0" * 64
    atomic_write_text(path, json.dumps(data, ensure_ascii=False))
    issues = doctor(project)
    assert any(x["severity"] == "ERROR" and x["code"] == "AI_READY_REVIEW_STALE"
               for x in issues)


@pytest.mark.parametrize("contents", ["{", "[]", '{"chapter":1}'])
def test_doctor_rejects_malformed_ready_report(project, contents):
    result = _ready(project)
    atomic_write_text(project.dir / result.report_path, contents)
    issues = doctor(project)
    assert any(x["severity"] == "ERROR" and x["code"] == "AI_READY_REVIEW_INVALID"
               for x in issues)


@pytest.mark.parametrize("unsafe_rel", ["review/ch0001.review.json", "review"])
def test_doctor_rejects_review_report_symlink_or_symlink_parent(project, monkeypatch, unsafe_rel):
    result = _ready(project)
    unsafe = project.dir / unsafe_rel
    original = type(unsafe).is_symlink
    monkeypatch.setattr(type(unsafe), "is_symlink",
                        lambda self: True if self == unsafe else original(self))
    issues = doctor(project)
    assert any(x["severity"] == "ERROR" and x["code"] == "UNSAFE_REVIEW_PATH"
               for x in issues)
