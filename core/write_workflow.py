"""M5 orchestration: validate -> Chief plan -> context -> Writer -> AI draft."""
from __future__ import annotations

import dataclasses
import json
from typing import Callable

from agents.planner import ChiefPlanningService
from agents.task_card import WritingTaskCard
from agents.writer import WriterRequest, WriterRunner
from core.ai_draft import AIChapterDraftService
from core.chapter import confirmed_path, draft_path, parse_frontmatter
from core.context import ContextItem, collect_project_context, collect_recent_chapter_metadata
from core.context_budget import ContextBudgetPlan, plan_context, render_writer_context
from core.generation import GenerationWorkspace, merge_continuation
from core.mutation import ABSENT, file_revision
from core.relevance import build_relevance_source, resolve_relevant_entities
from llm.provider import BaseProvider, CONTEXT_TOO_LONG, ProviderError
from llm.types import Usage


class WriteWorkflowError(Exception):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(f"{code}: {message}")


@dataclasses.dataclass
class WriteRequest:
    project: object
    chapter: int | None = None
    instruction: str = ""
    title: str = ""
    target_chars: int = 4000
    characters: list[str] = dataclasses.field(default_factory=list)
    world: list[str] = dataclasses.field(default_factory=list)
    mode: str = "new"
    plan_only: bool = False
    stream: bool = True


@dataclasses.dataclass
class WriteWorkflowResult:
    status: str
    chapter: int
    card: WritingTaskCard | None = None
    context_plan: ContextBudgetPlan | None = None
    writer_result: object | None = None
    draft_result: object | None = None
    chief_usages: list[Usage] = dataclasses.field(default_factory=list)
    writer_usages: list[Usage] = dataclasses.field(default_factory=list)
    warnings: list[str] = dataclasses.field(default_factory=list)
    partial_path: str = ""

    @property
    def usages(self) -> list[Usage]:
        return self.chief_usages + self.writer_usages


def _read(project, rel: str) -> str:
    path = project.store.safe_path(project.id, rel)
    return path.read_text(encoding="utf-8") if path.is_file() else ""


def _planner_items(project, chapter: int) -> list[ContextItem]:
    volume = int(project.metadata.get("current_volume", 1))
    items: list[ContextItem] = []
    for rel, typ, priority in (
        (f"outline/chapters/ch{chapter:04d}.md", "PLANNER_CHAPTER_OUTLINE", 120),
        ("rules/writing_rules.md", "PLANNER_RULES", 115),
        (f"outline/volumes/vol{volume:03d}.md", "PLANNER_VOLUME_OUTLINE", 105),
        ("outline/summary.md", "PLANNER_SUMMARY", 70),
    ):
        text = _read(project, rel)
        if text:
            path = project.store.safe_path(project.id, rel)
            items.append(ContextItem(rel, typ, priority, text, len(text),
                                     BaseProvider.estimate_tokens(text), file_revision(path)))
    metadata = json.dumps(collect_recent_chapter_metadata(project)[-10:], ensure_ascii=False)
    items.append(ContextItem("recent-chapter-metadata", "PLANNER_RECENT_METADATA", 60,
                             metadata, len(metadata), BaseProvider.estimate_tokens(metadata)))
    if project.current_chapter:
        recent = _read(project, f"chapters/ch{project.current_chapter:04d}.md")
        if recent:
            tail = recent[-1500:]
            items.append(ContextItem(
                f"chapters/ch{project.current_chapter:04d}.tail", "PLANNER_RECENT_TAIL", 90,
                tail, len(tail), BaseProvider.estimate_tokens(tail)))
    return items


def _load_item(project, rel: str, typ: str, priority: int) -> ContextItem:
    path = project.store.safe_path(project.id, rel)
    text = path.read_text(encoding="utf-8")
    return ContextItem(
        rel,
        typ,
        priority,
        text,
        len(text),
        BaseProvider.estimate_tokens(text),
        file_revision(path),
    )


class WriteWorkflow:
    def __init__(self, *, chief_provider, writer_provider, chief_prompt: str, writer_prompt: str, settings):
        self.chief_provider = chief_provider
        self.writer_provider = writer_provider
        self.settings = settings
        self.planner = ChiefPlanningService(chief_provider, chief_prompt)
        self.writer = WriterRunner(writer_provider, writer_prompt)

    def _context_plan(self, project, chapter: int, card: WritingTaskCard, req: WriteRequest,
                      existing: str, expected: str, continuation_text: str = "") -> ContextBudgetPlan:
        source = build_relevance_source(project, chapter, card, req.instruction)
        entities = resolve_relevant_entities(
            project,
            card,
            source,
            characters=req.characters,
            world=req.world,
        )
        items = collect_project_context(
            project,
            current_volume=int(project.metadata.get("current_volume", 1)),
            target_chapter=chapter,
            include_memory=True,
            recent_chapters=int(self.settings.context.get("max_recent_chapters", 5)),
            max_recent_chars=int(self.settings.context.get("max_recent_text_chars", 3000)),
        )
        existing_sources = {item.source for item in items}
        for rel in entities.characters:
            if rel not in existing_sources:
                items.append(_load_item(project, rel, "CHARACTER", 100))
        for rel in entities.world:
            if rel not in existing_sources:
                items.append(_load_item(project, rel, "WORLD", 100))
        if req.mode == "rewrite" and existing:
            items.append(
                ContextItem(
                    f"drafts/{draft_path(project, chapter).name}",
                    "CURRENT_DRAFT",
                    50,
                    existing,
                    len(existing),
                    BaseProvider.estimate_tokens(existing),
                    expected,
                )
            )
        if continuation_text:
            tail = continuation_text[-4000:]
            items.append(ContextItem(
                f"drafts/.generation/ch{chapter:04d}.continuation-tail",
                "CONTINUATION_TAIL", 108, tail, len(tail),
                BaseProvider.estimate_tokens(tail), expected,
            ))
        fixed = (
            BaseProvider.estimate_tokens(self.writer.system_prompt)
            + BaseProvider.estimate_tokens(json.dumps(card.to_dict(), ensure_ascii=False))
            + 128
        )
        cfg = self.writer_provider.config
        return plan_context(
            items,
            model_max_tokens=cfg.max_context_tokens,
            reserve_output_tokens=int(self.settings.context.get("reserve_output_tokens", 4096)),
            fixed_prompt_tokens=fixed,
        )

    def run(
        self,
        req: WriteRequest,
        *,
        on_stage: Callable[[str], None] | None = None,
        on_text_delta: Callable[[str], None] | None = None,
    ) -> WriteWorkflowResult:
        def stage(name: str) -> None:
            if on_stage:
                on_stage(name)

        project = req.project
        chapter = req.chapter or project.current_chapter + 1
        mode = req.mode
        if req.target_chars < 200:
            raise WriteWorkflowError("INVALID_TARGET_CHARS", "target chars must be at least 200")
        if mode not in {"new", "rewrite", "continue", "resume"}:
            raise WriteWorkflowError("INVALID_WRITE_MODE", mode)
        if confirmed_path(project, chapter).exists():
            raise WriteWorkflowError("CONFIRMED_PROTECTED", "confirmed chapter exists")

        target = draft_path(project, chapter)
        existing = ""
        expected = file_revision(target)
        if target.exists():
            old_meta, existing = parse_frontmatter(target.read_text(encoding="utf-8"))
            if old_meta.get("origin") != "ai":
                raise WriteWorkflowError("MANUAL_DRAFT_PROTECTED", "manual draft")
            if mode == "new":
                raise WriteWorkflowError("AI_DRAFT_EXISTS", "use --rewrite or --continue")
        elif mode in ("rewrite", "continue"):
            raise WriteWorkflowError("AI_DRAFT_NOT_FOUND", mode)
        if mode == "new" and chapter != project.current_chapter + 1:
            raise WriteWorkflowError("NON_CONTIGUOUS_CHAPTER", "new must target current chapter + 1")

        workspace = GenerationWorkspace(project, chapter)
        chief_usages: list[Usage] = []
        warnings: list[str] = []
        resume_origin_mode = ""
        if mode == "resume":
            if not workspace.partial.is_file() or not workspace.sidecar.is_file():
                raise WriteWorkflowError("PARTIAL_NOT_FOUND", "resume")
            try:
                sidecar = workspace.metadata()
                card = WritingTaskCard(**sidecar["resume_card"], user_instruction="", chief_brief="")
            except (KeyError, TypeError, ValueError) as exc:
                raise WriteWorkflowError("INVALID_PARTIAL_SIDECAR", str(exc)) from exc
            title = sidecar.get("title") or card.title
            provenance_task_hash = sidecar.get("task_hash", "")
            if not provenance_task_hash:
                raise WriteWorkflowError("INVALID_PARTIAL_SIDECAR", "missing task_hash")
            resume_origin_mode = sidecar.get("mode", "")
            if resume_origin_mode not in {"new", "rewrite", "continue"}:
                raise WriteWorkflowError("INVALID_PARTIAL_SIDECAR", "invalid original mode")
            expected = sidecar.get("base_revision", ABSENT)
            if file_revision(target) != expected:
                raise WriteWorkflowError("STALE_DRAFT_REVISION", "resume base changed")
            partial = workspace.text()
            if not partial:
                workspace.cleanup()
                raise WriteWorkflowError("EMPTY_PARTIAL", "partial contains no prose")
        else:
            chapter_outline = _read(project, f"outline/chapters/ch{chapter:04d}.md")
            if not chapter_outline.strip() and not req.instruction.strip():
                raise WriteWorkflowError(
                    "INSUFFICIENT_WRITING_PLAN", "需要章节细纲或 --instruction"
                )
            stage("Planning")
            planned = self.planner.plan(
                chapter=chapter,
                target_chars=req.target_chars,
                title=req.title,
                instruction=req.instruction,
                project_items=_planner_items(project, chapter),
            )
            card = planned.card
            provenance_task_hash = card.task_hash
            chief_usages.extend(planned.usages)
            title = req.title or card.title or f"第{chapter}章"
            partial = ""

        stage("Context")
        continuation_text = ""
        if mode == "continue":
            continuation_text = existing
        elif mode == "resume":
            continuation_text = (merge_continuation(existing, partial)
                                 if resume_origin_mode == "continue" else partial)
        context_plan = self._context_plan(
            project, chapter, card, req, existing, expected, continuation_text)
        if mode == "resume":
            previous_hash = sidecar.get("context_hash", "")
            if previous_hash and previous_hash != context_plan.context_hash:
                warnings.append("项目上下文自中断后可能已变化；本次使用当前上下文继续。")
        if req.plan_only:
            return WriteWorkflowResult(
                "planned", chapter, card, context_plan, chief_usages=chief_usages
            )

        metadata = {
            "mode": mode,
            "title": title,
            "base_revision": expected,
            "task_hash": provenance_task_hash,
            "context_hash": context_plan.context_hash,
            "model": self.writer_provider.config.model,
            "resume_card": card.resume_dict(),
        }
        if mode != "resume":
            workspace.prepare(metadata)

        stage("Writing")
        writer_req = WriterRequest(
            project,
            chapter,
            title,
            card,
            context_plan,
            card.target_chars,
            mode,
            existing_text=(merge_continuation(existing, partial)
                           if mode == "resume" and resume_origin_mode == "continue"
                           else partial if mode == "resume" else existing),
            additional_instruction=req.instruction,
            base_revision=expected,
            provenance_task_hash=provenance_task_hash,
        )
        try:
            try:
                writer_result = self.writer.run(
                    writer_req,
                    rendered_context=render_writer_context(context_plan),
                    on_text_delta=on_text_delta,
                    workspace=workspace,
                    stream=req.stream,
                )
            except ProviderError as exc:
                if exc.code != CONTEXT_TOO_LONG or workspace.text():
                    raise
                context_plan = context_plan.shrink(0.65)
                writer_req.context_plan = context_plan
                workspace.update_metadata(context_hash=context_plan.context_hash)
                writer_result = self.writer.run(
                    writer_req,
                    rendered_context=render_writer_context(context_plan),
                    on_text_delta=on_text_delta,
                    workspace=workspace,
                    stream=req.stream,
                )
        except BaseException:
            # An empty workspace is useless; a non-empty one is resumable.
            if not workspace.text():
                workspace.cleanup()
            raise

        writer_usages = [writer_result.usage] if writer_result.usage else []
        if writer_result.generation_state == "interrupted":
            return WriteWorkflowResult(
                "interrupted",
                chapter,
                card,
                context_plan,
                writer_result,
                chief_usages=chief_usages,
                writer_usages=writer_usages,
                warnings=warnings,
                partial_path=workspace.partial.relative_to(project.dir).as_posix(),
            )

        generated = writer_result.text
        if mode == "continue":
            final_body = merge_continuation(existing, generated)
        elif mode == "resume":
            resumed_body = merge_continuation(partial, generated)
            final_body = (merge_continuation(existing, resumed_body)
                          if resume_origin_mode == "continue" else resumed_body)
        else:
            final_body = generated
        stage("Saving")
        draft = AIChapterDraftService(project).finalize(
            chapter=chapter,
            title=title,
            body=final_body,
            mode=mode,
            generation_state=writer_result.generation_state,
            model=writer_result.model,
            context_hash=context_plan.context_hash,
            task_hash=provenance_task_hash,
            expected_revision=expected,
            characters=card.characters,
        )
        warnings.extend(workspace.cleanup())
        return WriteWorkflowResult(
            "saved",
            chapter,
            card,
            context_plan,
            writer_result,
            draft,
            chief_usages,
            writer_usages,
            warnings,
        )
