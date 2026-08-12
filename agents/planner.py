"""Read-only Chief planning service; never owns project mutation tools."""
from __future__ import annotations
import json
from dataclasses import dataclass
from llm.provider import BaseProvider, ProviderError
from llm.types import ChatMessage, Usage
from .task_card import WritingTaskCard, TaskCardError, parse_task_card


@dataclass
class PlanningResult:
    card: WritingTaskCard
    model: str
    usages: list[Usage]
    repaired: bool = False


class ChiefPlanningService:
    def __init__(self, provider: BaseProvider, system_prompt: str):
        self.provider, self.system_prompt = provider, system_prompt

    def plan(self, *, chapter: int, target_chars: int, title: str, instruction: str,
             project_data: str) -> PlanningResult:
        schema = {"chapter":"integer","goal":"string","target_chars":"integer","title":"string",
                  "opening":"string","conflict":"string","turning_point":"string","ending_hook":"string",
                  "characters":"list[string]","world_elements":"list[string]",
                  "continuity_requirements":"list[string]","style_requirements":"list[string]",
                  "forbidden_changes":"list[string]","user_instruction":"string","chief_brief":"string"}
        user = (f"目标章: {chapter}\n目标字数: {target_chars}\n指定标题: {title}\n用户要求: {instruction}\n"
                f"Schema: {json.dumps(schema, ensure_ascii=False)}\n[PLANNING_DATA_BEGIN]\n{project_data}\n[PLANNING_DATA_END]")
        messages = [ChatMessage("system", self.system_prompt), ChatMessage("user", user)]
        resp = self.provider.chat(messages, tools=None)
        usages = [resp.usage] if resp.usage else []
        try: card = parse_task_card(resp.text); repaired = False
        except TaskCardError:
            repair = self.provider.chat([ChatMessage("system", "只把输入修正为合法 JSON object，不添加解释。"),
                ChatMessage("user", f"Schema: {json.dumps(schema, ensure_ascii=False)}\n失败结果:\n{resp.text}")], tools=None)
            if repair.usage: usages.append(repair.usage)
            try: card = parse_task_card(repair.text); repaired = True
            except TaskCardError:
                goal = instruction.strip() or title.strip() or "依据本章细纲推进故事"
                card = WritingTaskCard(chapter, goal, target_chars, title=title, user_instruction=instruction,
                                       chief_brief="依据本章细纲、写作规则与用户要求推进。", source="fallback")
                repaired = True
        if card.chapter != chapter or card.target_chars != target_chars:
            data = card.to_dict() | {"chapter":chapter, "target_chars":target_chars,
                                     "title": card.title or title}
            card = WritingTaskCard(**data)
        return PlanningResult(card, resp.model, usages, repaired)
