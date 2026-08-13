from __future__ import annotations

from types import SimpleNamespace

import pytest

from core.ai_draft import AIChapterDraftService
from core.chapter import confirm_draft, draft_path, write_draft
from core.config import ModelConfig
from core.context_budget import render_review_context
from core.mutation import ABSENT, file_revision
from core.project import create_project, validate_project
from core.relevance import RelevanceError
from core.review_context import ReviewContextBuilder, render_review_request
from core.review_preflight import ReviewPreflight, merge_preflight_issues
from core.storage import ProjectStore, atomic_write_text
from llm.provider import BaseProvider


class StubProvider(BaseProvider):
    def __init__(self, max_context: int = 16_000):
        super().__init__(ModelConfig(model="reviewer", max_context_tokens=max_context))


@pytest.fixture
def project(tmp_path):
    p = create_project(ProjectStore(tmp_path / "novels"), "M6", project_id="m6-novel")
    atomic_write_text(p.dir / "outline/summary.md", "# Summary\nA closed-room mystery.")
    atomic_write_text(p.dir / "outline/volumes/vol001.md", "# Volume One\nLin enters the tower.")
    atomic_write_text(p.dir / "outline/chapters/ch0001.md", "# Chapter One\nLin finds the Rune Gate.")
    atomic_write_text(p.dir / "rules/writing_rules.md", "# Rules\nKeep viewpoint limited.")
    atomic_write_text(p.dir / "characters/lin.md", "# Lin\nA careful investigator.")
    atomic_write_text(p.dir / "world/rune-gate.md", "# Rune Gate\nIt opens only at dusk.")
    atomic_write_text(p.dir / "memory/index.md", "# Memory\nDerived recollections. " * 200)
    AIChapterDraftService(p).finalize(
        chapter=1, title="One", body="Lin studies the Rune Gate at dusk.", mode="new",
        generation_state="complete", model="writer", context_hash="c" * 64,
        task_hash="t" * 64, expected_revision=ABSENT,
    )
    return p


def builder(max_context=16_000, **overrides):
    values = dict(reserve_output_tokens=1024, max_recent_chapters=2,
                  max_recent_text_chars=500)
    values.update(overrides)
    return ReviewContextBuilder(StubProvider(max_context), "review system", **values)


def test_review_context_contains_subject_facts_relevance_and_provenance(project):
    plan = builder().build(project, 1)
    selected = {(item.type, item.source) for item in plan.selected_items}
    assert ("REVIEW_DRAFT", "drafts/ch0001.draft.md") in selected
    assert ("RULES", "rules/writing_rules.md") in selected
    assert ("CHAPTER_OUTLINE", "outline/chapters/ch0001.md") in selected
    assert ("CHARACTER", "characters/lin.md") in selected
    assert ("WORLD", "world/rune-gate.md") in selected
    assert plan.relevance.characters == ["characters/lin.md"]
    assert plan.relevance.world == ["world/rune-gate.md"]
    rendered = render_review_context(plan.shared)
    assert "[REVIEW_SUBJECT:drafts/ch0001.draft.md]" in rendered
    assert "[PROVENANCE:drafts/ch0001.draft.md#provenance]" in rendered
    assert "task_hash" in rendered


def test_context_optional_overrides_and_recent_are_bounded_not_full_novel(project):
    atomic_write_text(project.dir / "characters/mei.md", "# Mei\nAn archivist.")
    for number in (2, 3, 4):
        write_draft(project, number, str(number), f"confirmed-{number}-" + "x" * 100)
        confirm_draft(project, number)
    plan = builder(max_recent_chapters=2, max_recent_text_chars=140).build(
        project, 1, characters=["Mei"])
    assert "characters/mei.md" in [x.source for x in plan.selected_items]
    recent = [x for x in plan.selected_items if x.type == "RECENT_CHAPTER"]
    assert len(recent) <= 2
    assert sum(x.included_chars for x in recent) <= 140
    assert not any(x.source == "chapters/ch0002.md" for x in recent)


def test_review_budget_is_actual_render_strict_stable_and_drops_memory_first(project):
    p1 = builder(4000, reserve_output_tokens=600).build(project, 1)
    p2 = builder(4000, reserve_output_tokens=600).build(project, 1)
    assert p1.context_hash == p2.context_hash
    assert [x.source for x in p1.selected_items] == [x.source for x in p2.selected_items]
    actual = BaseProvider.estimate_messages_tokens([
        SimpleNamespace(content="review system", tool_calls=None, tool_call_id=None),
        SimpleNamespace(content=render_review_request(p1), tool_calls=None, tool_call_id=None),
    ])
    assert actual + p1.shared.reserve_output_tokens + p1.shared.safety_margin_tokens <= 4000
    if p1.dropped_items:
        assert all(x.type not in {"REVIEW_DRAFT", "RULES", "CHAPTER_OUTLINE"}
                   for x in p1.dropped_items)


def test_review_budget_counts_the_exact_messages_sent_by_runner(project):
    from agents.reviewer import ReviewRequest, ReviewerRunner
    from llm.types import ChatResult

    payload = {
        "chapter": 1, "verdict": "PASS", "summary": "ok", "issues": [],
        "strengths": [], "task_fulfillment": "ok", "continuity_assessment": "ok",
        "style_assessment": "ok", "logic_assessment": "ok", "confidence": 1.0,
        "source": "reviewer",
    }
    class CapturingProvider(StubProvider):
        def __init__(self):
            super().__init__(4000)
            self.messages = None
        def chat(self, messages, **kwargs):
            import json
            self.messages = messages
            return ChatResult(json.dumps(payload), model="reviewer")

    provider = CapturingProvider()
    plan = ReviewContextBuilder(provider, "review system", reserve_output_tokens=600).build(project, 1)
    ReviewerRunner(provider, "review system").run(
        ReviewRequest(project, 1, plan.draft_revision, plan),
        rendered_context=render_review_context(plan.shared))
    actual = BaseProvider.estimate_messages_tokens(provider.messages)
    assert actual + plan.shared.reserve_output_tokens + plan.shared.safety_margin_tokens <= 4000


def test_review_specific_budget_drops_memory_before_other_noncritical_sources(project):
    plan = builder(2550, reserve_output_tokens=400).build(project, 1)
    dropped = [x.type for x in plan.dropped_items]
    assert "MEMORY" in dropped
    assert "PROJECT" not in dropped
    assert "REVIEW_PROVENANCE" not in dropped


def test_huge_review_draft_uses_deterministic_head_middle_tail_marker(project):
    chapter = draft_path(project, 1)
    text = chapter.read_text(encoding="utf-8")
    header, _ = text.split("---\n", 2)[1:]
    # Preserve valid frontmatter while replacing only the body.
    prefix = text[:text.find("---\n", 4) + 4]
    huge = "HEAD-ANCHOR\n" + "a" * 20_000 + "\nMIDDLE-ANCHOR\n" + "b" * 20_000 + "\nTAIL-ANCHOR"
    atomic_write_text(chapter, prefix + huge)
    plan = builder(7000, reserve_output_tokens=600).build(project, 1)
    draft = next(x for x in plan.selected_items if x.type == "REVIEW_DRAFT")
    assert draft.status == "TRUNCATE"
    assert "DRAFT_TRUNCATED_FOR_REVIEW" in draft.text
    assert "HEAD-ANCHOR" in draft.text
    assert "MIDDLE-ANCHOR" in draft.text
    assert "TAIL-ANCHOR" in draft.text
    assert plan.draft_truncated
    assert plan.context_hash == builder(7000, reserve_output_tokens=600).build(project, 1).context_hash
    preflight = ReviewPreflight().run(project, 1, context_plan=plan)
    assert any(x.code == "DRAFT_TRUNCATED_FOR_REVIEW" for x in preflight.blockers)
    assert preflight.can_review and not preflight.can_ready


def test_review_preflight_happy_missing_outline_and_issue_merge(project):
    result = ReviewPreflight().run(project, 1)
    assert result.can_review and result.can_ready
    (project.dir / "outline/chapters/ch0001.md").unlink()
    result = ReviewPreflight().run(project, 1)
    assert any(x.code == "MISSING_CHAPTER_OUTLINE" and x.severity == "WARNING"
               for x in result.issues)
    merged = merge_preflight_issues(result.issues, [{"severity": "MAJOR", "code": "LLM"}])
    assert merged[0].code == "MISSING_CHAPTER_OUTLINE"
    assert merged[-1]["code"] == "LLM"


@pytest.mark.parametrize("mutation,code", [
    ("manual", "MANUAL_DRAFT_NOT_REVIEWABLE"),
    ("empty", "EMPTY_DRAFT_BODY"),
    ("generation", "INVALID_GENERATION_STATE"),
    ("chapter", "DRAFT_CHAPTER_MISMATCH"),
])
def test_review_preflight_rejects_bad_draft(project, mutation, code):
    path = draft_path(project, 1)
    text = path.read_text(encoding="utf-8")
    if mutation == "manual":
        text = text.replace("origin: ai", "origin: manual")
    elif mutation == "empty":
        text = text[:text.find("---\n", 4) + 4]
    elif mutation == "generation":
        text = text.replace("generation_state: complete", "generation_state: broken")
    else:
        text = text.replace("chapter: 1", "chapter: 2", 1)
    atomic_write_text(path, text)
    result = ReviewPreflight().run(project, 1)
    assert any(x.code == code and x.severity == "BLOCKER" for x in result.issues)
    assert not result.can_ready


def test_review_preflight_invalid_frontmatter_confirmed_conflict_and_doctor_error(project):
    path = draft_path(project, 1)
    original = path.read_bytes()
    path.write_bytes(b"not frontmatter")
    assert any(x.code == "INVALID_DRAFT" for x in ReviewPreflight().run(project, 1).issues)
    path.write_bytes(original)
    write_draft(project, 2, "two", "manual")
    confirm_draft(project, 2)
    (project.dir / "chapters/ch0002.md").replace(project.dir / "chapters/ch0001.md")
    result = ReviewPreflight().run(project, 1)
    assert any(x.code == "CONFIRMED_CHAPTER_CONFLICT" for x in result.issues)
    assert any(x.code == "PROJECT_INTEGRITY" for x in result.issues)


def test_review_preflight_duplicate_h1_is_blocker(project):
    atomic_write_text(project.dir / "characters/other.md", "# Lin\nDuplicate.")
    result = ReviewPreflight().run(project, 1)
    assert any(x.code == "DUPLICATE_CHARACTER_H1" and x.severity == "BLOCKER"
               for x in result.issues)


def test_relevance_world_override_missing_and_ambiguous(project):
    assert "world/rune-gate.md" in builder().build(project, 1, world=["Rune Gate"]).relevance.world
    with pytest.raises(RelevanceError, match="WORLD_NOT_FOUND"):
        builder().build(project, 1, world=["Missing Place"])
    atomic_write_text(project.dir / "world/other.md", "# Rune Gate\nDuplicate.")
    with pytest.raises(RelevanceError, match="AMBIGUOUS_WORLD"):
        builder().build(project, 1, world=["Rune Gate"])


def test_preflight_invalid_utf8_and_non_draft_statuses(project):
    path = draft_path(project, 1)
    original = path.read_bytes()
    path.write_bytes(original + b"\xff")
    assert any(x.code == "INVALID_UTF8" for x in ReviewPreflight().run(project, 1).blockers)
    path.write_bytes(original)
    text = path.read_text(encoding="utf-8")
    for status in ("ready", "reviewing", "confirmed"):
        atomic_write_text(path, text.replace("status: draft", f"status: {status}"))
        assert any(x.code == "INVALID_DRAFT_STATUS"
                   for x in ReviewPreflight().run(project, 1).blockers)


def test_project_validation_is_origin_aware_for_m6_states(project):
    path = draft_path(project, 1)
    text = path.read_text(encoding="utf-8").replace("status: draft", "status: ready")
    atomic_write_text(path, text)
    assert not any("status=" in issue for issue in validate_project(project.store, project.id))
    atomic_write_text(path, text.replace("origin: ai", "origin: manual"))
    assert any("status=" in issue for issue in validate_project(project.store, project.id))
