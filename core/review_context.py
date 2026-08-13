"""Bounded, provenance-aware context assembly for the Reviewer."""
from __future__ import annotations

import dataclasses
import json
from typing import Iterable

from llm.provider import BaseProvider

from .chapter import Chapter, read_draft
from .context import ContextItem, collect_project_context
from .context_budget import (
    ContextBudgetPlan,
    PlannedContextItem,
    plan_context,
    render_review_context,
)
from .mutation import file_revision
from .relevance import RelevantEntities, resolve_relevant_paths

REVIEW_OUTPUT_RESERVE_TOKENS = 4096


@dataclasses.dataclass
class ReviewContextPlan:
    shared: ContextBudgetPlan
    chapter: int
    instruction: str
    draft_revision: str
    relevance: RelevantEntities

    @property
    def selected_items(self) -> list[PlannedContextItem]:
        return self.shared.selected_items

    @property
    def dropped_items(self) -> list[PlannedContextItem]:
        return self.shared.dropped_items

    @property
    def truncated_items(self) -> list[PlannedContextItem]:
        return self.shared.truncated_items

    @property
    def context_hash(self) -> str:
        return self.shared.context_hash

    @property
    def draft_truncated(self) -> bool:
        return any(x.type == "REVIEW_DRAFT" and x.was_truncated
                   for x in self.shared.selected_items)

    def shrink(self, factor: float = 0.65) -> "ReviewContextPlan":
        return dataclasses.replace(self, shared=self.shared.shrink(factor))


def render_review_request(plan: ReviewContextPlan) -> str:
    """Render the exact canonical user message used by ReviewerRunner."""
    from agents.reviewer import build_review_messages
    return build_review_messages(
        "", chapter=plan.chapter, instruction=plan.instruction,
        rendered_context=render_review_context(plan.shared))[1].content


def _load_item(project, rel: str, typ: str, priority: int) -> ContextItem:
    path = project.store.safe_path(project.id, rel)
    text = path.read_text(encoding="utf-8")
    return ContextItem(
        rel, typ, priority, text, len(text), BaseProvider.estimate_tokens(text),
        file_revision(path),
    )


def _review_relevance_source(project, chapter: int, body: str, instruction: str) -> str:
    volume = int(project.metadata.get("current_volume", 1) or 1)
    chunks = [body]
    for rel in (f"outline/chapters/ch{chapter:04d}.md",
                f"outline/volumes/vol{volume:03d}.md"):
        path = project.store.safe_path(project.id, rel)
        if path.is_file():
            chunks.append(path.read_text(encoding="utf-8"))
    if instruction:
        chunks.append(instruction)
    return "\n\n".join(chunks)


class ReviewContextBuilder:
    def __init__(self, provider: BaseProvider, system_prompt: str, *,
                 reserve_output_tokens: int = REVIEW_OUTPUT_RESERVE_TOKENS,
                 max_recent_chapters: int = 5,
                 max_recent_text_chars: int = 3000):
        self.provider = provider
        self.system_prompt = system_prompt
        self.reserve_output_tokens = reserve_output_tokens
        self.max_recent_chapters = max_recent_chapters
        self.max_recent_text_chars = max_recent_text_chars

    def build(self, project, chapter: int, *, instruction: str = "",
              characters: Iterable[str] | None = None,
              world: Iterable[str] | None = None,
              draft: Chapter | None = None,
              draft_revision: str | None = None) -> ReviewContextPlan:
        draft = draft or read_draft(project, chapter)
        if draft.number != chapter:
            raise ValueError("DRAFT_CHAPTER_MISMATCH")
        revision = draft_revision or file_revision(draft.path)
        source = _review_relevance_source(project, chapter, draft.body, instruction)
        relevance = resolve_relevant_paths(
            project, source, characters=list(characters or []), world=list(world or []))

        items = collect_project_context(
            project,
            current_volume=int(project.metadata.get("current_volume", 1) or 1),
            target_chapter=chapter,
            include_memory=True,
            recent_chapters=self.max_recent_chapters,
            max_recent_chars=self.max_recent_text_chars,
        )
        existing = {item.source for item in items}
        for rel in relevance.characters:
            if rel not in existing:
                items.append(_load_item(project, rel, "CHARACTER", 100))
                existing.add(rel)
        for rel in relevance.world:
            if rel not in existing:
                items.append(_load_item(project, rel, "WORLD", 100))
                existing.add(rel)

        draft_rel = f"drafts/{draft.path.name}"
        items.append(ContextItem(
            draft_rel, "REVIEW_DRAFT", 130, draft.body, len(draft.body),
            BaseProvider.estimate_tokens(draft.body), revision,
        ))
        provenance = json.dumps({
            key: draft.meta[key]
            for key in ("generation_model", "generation_mode", "generation_state",
                        "context_hash", "task_hash")
            if key in draft.meta
        }, ensure_ascii=False, sort_keys=True)
        items.append(ContextItem(
            f"{draft_rel}#provenance", "REVIEW_PROVENANCE", 20, provenance,
            len(provenance), BaseProvider.estimate_tokens(provenance), revision,
        ))

        from agents.reviewer import build_review_messages
        fixed = BaseProvider.estimate_messages_tokens(build_review_messages(
            self.system_prompt, chapter=chapter, instruction=instruction,
            rendered_context="")) + 16
        shared = plan_context(
            items,
            model_max_tokens=self.provider.config.max_context_tokens,
            reserve_output_tokens=self.reserve_output_tokens,
            fixed_prompt_tokens=fixed,
            render_profile="review",
        )
        review_drafts = [item for item in shared.selected_items if item.type == "REVIEW_DRAFT"]
        if not review_drafts:
            raise ValueError("REVIEW_DRAFT_CONTEXT_EXHAUSTED")
        if (review_drafts[0].status == "TRUNCATE"
                and "DRAFT_TRUNCATED_FOR_REVIEW" not in review_drafts[0].text):
            raise ValueError("REVIEW_DRAFT_CONTEXT_EXHAUSTED")
        return ReviewContextPlan(shared, chapter, instruction, revision, relevance)
