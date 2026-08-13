"""Read-only, strictly bounded Chief planning service."""
from __future__ import annotations

import json
from dataclasses import dataclass

from core.context import ContextItem
from core.context_budget import ContextBudgetPlan, plan_context, render_writer_context
from llm.provider import BaseProvider, CONTEXT_TOO_LONG, ProviderError
from llm.types import ChatMessage, Usage
from .task_card import WritingTaskCard, TaskCardError, parse_task_card

PLANNER_OUTPUT_RESERVE_TOKENS = 2048

TASK_CARD_SCHEMA = {
    "chapter": "integer", "goal": "string", "target_chars": "integer", "title": "string",
    "opening": "string", "conflict": "string", "turning_point": "string",
    "ending_hook": "string", "characters": "list[string]", "world_elements": "list[string]",
    "continuity_requirements": "list[string]", "style_requirements": "list[string]",
    "forbidden_changes": "list[string]", "user_instruction": "string", "chief_brief": "string",
}


@dataclass
class PlanningResult:
    card: WritingTaskCard
    model: str
    usages: list[Usage]
    repaired: bool = False
    context_plan: ContextBudgetPlan | None = None


def _planner_wrapper(chapter: int, target_chars: int, title: str, instruction: str,
                     rendered_context: str) -> str:
    return (
        f"目标章: {chapter}\n目标字数: {target_chars}\n指定标题: {title}\n用户要求: {instruction}\n"
        f"Schema: {json.dumps(TASK_CARD_SCHEMA, ensure_ascii=False)}\n"
        f"[PLANNING_DATA_BEGIN]\n{rendered_context}\n[PLANNING_DATA_END]"
    )


def build_planner_context_plan(provider: BaseProvider, system_prompt: str, *, chapter: int,
                               target_chars: int, title: str, instruction: str,
                               project_items: list[ContextItem]) -> ContextBudgetPlan:
    empty_wrapper = _planner_wrapper(chapter, target_chars, title, instruction, "")
    fixed = (BaseProvider.estimate_tokens(system_prompt)
             + BaseProvider.estimate_tokens(empty_wrapper) + 16)
    return plan_context(
        project_items,
        model_max_tokens=provider.config.max_context_tokens,
        reserve_output_tokens=PLANNER_OUTPUT_RESERVE_TOKENS,
        fixed_prompt_tokens=fixed,
    )


class ChiefPlanningService:
    def __init__(self, provider: BaseProvider, system_prompt: str):
        self.provider, self.system_prompt = provider, system_prompt

    def plan(self, *, chapter: int, target_chars: int, title: str, instruction: str,
             project_data: str = "", project_items: list[ContextItem] | None = None) -> PlanningResult:
        items = project_items or [ContextItem(
            "planner/project-data", "PLANNER_SUMMARY", 1, project_data, len(project_data),
            BaseProvider.estimate_tokens(project_data), None,
        )]
        context_plan = build_planner_context_plan(
            self.provider, self.system_prompt, chapter=chapter, target_chars=target_chars,
            title=title, instruction=instruction, project_items=items)
        usages: list[Usage] = []

        def request(plan: ContextBudgetPlan):
            user = _planner_wrapper(
                chapter, target_chars, title, instruction, render_writer_context(plan))
            return self.provider.chat(
                [ChatMessage("system", self.system_prompt), ChatMessage("user", user)], tools=None)

        try:
            resp = request(context_plan)
        except ProviderError as exc:
            if exc.code != CONTEXT_TOO_LONG:
                raise
            context_plan = context_plan.shrink(0.65)
            resp = request(context_plan)  # exactly one context-overflow retry
        if resp.usage:
            usages.append(resp.usage)
        try:
            card = parse_task_card(resp.text)
            repaired = False
        except TaskCardError:
            # Repair is a separate, bounded call: schema + failed JSON only.
            repair = self.provider.chat([
                ChatMessage("system", "只把输入修正为合法 JSON object，不添加解释。"),
                ChatMessage("user", f"Schema: {json.dumps(TASK_CARD_SCHEMA, ensure_ascii=False)}\n失败结果:\n{resp.text}"),
            ], tools=None)
            if repair.usage:
                usages.append(repair.usage)
            try:
                card = parse_task_card(repair.text)
                repaired = True
            except TaskCardError:
                goal = instruction.strip() or title.strip() or "依据本章细纲推进故事"
                card = WritingTaskCard(
                    chapter, goal, target_chars, title=title, user_instruction=instruction,
                    chief_brief="依据本章细纲、写作规则与用户要求推进。", source="fallback")
                repaired = True
        if card.chapter != chapter or card.target_chars != target_chars:
            data = card.to_dict() | {
                "chapter": chapter, "target_chars": target_chars, "title": card.title or title}
            card = WritingTaskCard(**data)
        return PlanningResult(card, resp.model, usages, repaired, context_plan)
