from __future__ import annotations

import json

import pytest

from core.ai_draft import AIChapterDraftService
from core.chapter import draft_path, parse_frontmatter
from core.config import ModelConfig, Settings
from core.mutation import ABSENT
from core.project import create_project
from core.review import ReviewError
from core.review_workflow import (
    ReviewWorkflow,
    ReviewWorkflowError,
    ReviewWorkflowRequest,
)
from core.storage import ProjectStore, atomic_write_text
from llm.provider import BaseProvider, CONTEXT_TOO_LONG, ProviderError
from llm.types import ChatResult


def report(*, verdict="PASS", issues=None):
    return json.dumps({
        "chapter": 1, "verdict": verdict, "summary": "ok", "issues": issues or [],
        "strengths": [], "task_fulfillment": "ok", "continuity_assessment": "ok",
        "style_assessment": "ok", "logic_assessment": "ok", "confidence": 0.9,
        "source": "reviewer",
    })


class FakeProvider(BaseProvider):
    def __init__(self, replies, *, max_context=16_000, on_call=None):
        super().__init__(ModelConfig(model="reviewer", max_context_tokens=max_context))
        self.replies = list(replies)
        self.calls = []
        self.on_call = on_call

    def chat(self, messages, *, temperature=None, tools=None):
        self.calls.append(messages)
        if self.on_call:
            self.on_call(len(self.calls))
        value = self.replies.pop(0)
        if isinstance(value, BaseException):
            raise value
        return value


@pytest.fixture
def project(tmp_path):
    p = create_project(ProjectStore(tmp_path / "novels"), "M6", project_id="m6-workflow")
    atomic_write_text(p.dir / "outline/summary.md", "# Summary\nMystery.")
    atomic_write_text(p.dir / "outline/volumes/vol001.md", "# Volume\nTower.")
    atomic_write_text(p.dir / "outline/chapters/ch0001.md", "# Chapter\nEnter tower.")
    atomic_write_text(p.dir / "rules/writing_rules.md", "# Rules\nLimited POV.")
    atomic_write_text(p.dir / "characters/lin.md", "# Lin\nInvestigator.")
    AIChapterDraftService(p).finalize(
        chapter=1, title="One", body="Lin enters the tower.\nThe bell rings.\n", mode="new",
        generation_state="complete", model="writer", context_hash="c" * 64,
        task_hash="t" * 64, expected_revision=ABSENT,
    )
    return p


def settings(tmp_path):
    value = Settings.load(tmp_path / "settings.json")
    value.context["review_reserve_output_tokens"] = 600
    return value


def workflow(provider, settings_obj):
    return ReviewWorkflow(reviewer_provider=provider, reviewer_prompt="review system",
                          settings=settings_obj)


def tree_bytes(root):
    return {x.relative_to(root).as_posix(): x.read_bytes() for x in root.rglob("*") if x.is_file()}


def test_plan_only_uses_default_chapter_and_has_zero_provider_or_mutation(project, tmp_path):
    provider = FakeProvider([])
    before = tree_bytes(project.dir)
    result = workflow(provider, settings(tmp_path)).run(
        ReviewWorkflowRequest(project, plan_only=True, instruction="check voice",
                              characters=["Lin"]))
    assert result.status == "planned" and result.chapter == project.current_chapter + 1 == 1
    assert result.context_plan.instruction == "check voice"
    assert "characters/lin.md" in result.context_plan.relevance.characters
    assert provider.calls == []
    assert tree_bytes(project.dir) == before


def test_preflight_and_llm_issues_merge_into_persisted_report(project, tmp_path):
    (project.dir / "outline/chapters/ch0001.md").unlink()
    minor = {"id": "llm", "category": "STYLE", "severity": "MINOR", "title": "word",
             "description": "repeat", "location": {"line_start": 1, "line_end": 1, "anchor": "Lin"},
             "evidence": "Lin", "suggestion": "remove repeat"}
    provider = FakeProvider([ChatResult(report(issues=[minor]), model="reviewer")])
    result = workflow(provider, settings(tmp_path)).run(ReviewWorkflowRequest(project, chapter=1))
    artifact = json.loads((project.dir / result.review_result.report_path).read_text(encoding="utf-8"))
    assert [x["id"] for x in artifact["issues"]] == ["llm", "MISSING_CHAPTER_OUTLINE"]
    assert artifact["verdict"] == "PASS"


@pytest.mark.parametrize("mode", ["doctor", "truncated"])
def test_deterministic_blockers_force_needs_work_even_when_model_passes(project, tmp_path, mode):
    max_context = 16_000
    if mode == "doctor":
        atomic_write_text(project.dir / "characters/duplicate.md", "# Lin\nDuplicate identity.")
    else:
        path = draft_path(project, 1)
        text = path.read_text(encoding="utf-8")
        prefix = text[:text.find("---\n", 4) + 4]
        atomic_write_text(path, prefix + "HEAD\n" + "x" * 30_000 + "\nTAIL")
        max_context = 5_000
    provider = FakeProvider([ChatResult(report(), model="reviewer")], max_context=max_context)
    result = workflow(provider, settings(tmp_path)).run(ReviewWorkflowRequest(project, 1))
    assert result.report.verdict == "NEEDS_WORK"
    assert any(x.severity == "BLOCKER" for x in result.report.issues)
    assert parse_frontmatter(draft_path(project, 1).read_text(encoding="utf-8"))[0]["status"] == "draft"


def test_context_too_long_retries_once_with_point65_smaller_plan(project, tmp_path):
    overflow = ProviderError(CONTEXT_TOO_LONG, "too long")
    provider = FakeProvider([overflow, ChatResult(report(), model="reviewer")])
    result = workflow(provider, settings(tmp_path)).run(ReviewWorkflowRequest(project, 1))
    assert len(provider.calls) == 2
    assert len(provider.calls[1][1].content) < len(provider.calls[0][1].content)
    assert result.context_retried and result.context_plan.shared.estimated_input_tokens > 0
    assert result.context_plan.draft_truncated
    assert result.report.verdict == "NEEDS_WORK"
    assert any(x.id == "DRAFT_TRUNCATED_FOR_REVIEW" for x in result.report.issues)
    artifact = json.loads((project.dir / result.review_result.report_path).read_text(encoding="utf-8"))
    assert artifact["context_hash"] == result.context_plan.context_hash


@pytest.mark.parametrize("failure", ["double_overflow", "malformed", "provider", "interrupt"])
def test_failures_abort_active_review_and_never_ready(project, tmp_path, failure):
    if failure == "double_overflow":
        replies = [ProviderError(CONTEXT_TOO_LONG, "one"), ProviderError(CONTEXT_TOO_LONG, "two")]
        expected = ProviderError
    elif failure == "malformed":
        replies = [ChatResult("bad"), ChatResult("still bad")]
        expected = ReviewWorkflowError
    elif failure == "provider":
        replies = [ProviderError("NETWORK_ERROR", "offline")]
        expected = ProviderError
    else:
        replies = [KeyboardInterrupt()]
        expected = KeyboardInterrupt
    provider = FakeProvider(replies)
    with pytest.raises(expected):
        workflow(provider, settings(tmp_path)).run(ReviewWorkflowRequest(project, 1))
    assert parse_frontmatter(draft_path(project, 1).read_text(encoding="utf-8"))[0]["status"] == "draft"
    # A successful follow-up proves the in-memory active run was aborted.
    followup = FakeProvider([ChatResult(report(), model="reviewer")])
    workflow(followup, settings(tmp_path)).run(ReviewWorkflowRequest(project, 1))


def test_external_edit_before_response_causes_stale_finalize_without_overwrite(project, tmp_path):
    path = draft_path(project, 1)
    def edit(_):
        atomic_write_text(path, path.read_text(encoding="utf-8") + "external edit")
    provider = FakeProvider([ChatResult(report(), model="reviewer")], on_call=edit)
    with pytest.raises(ReviewError, match="STALE_REVIEW_DRAFT"):
        workflow(provider, settings(tmp_path)).run(ReviewWorkflowRequest(project, 1))
    assert path.read_text(encoding="utf-8").endswith("external edit")
