"""Agent 定义(M3 只实例化 Chief; Writer/Reviewer 未来里程碑再建, 不创建可运行实例)。"""
from __future__ import annotations

from pathlib import Path

from agents.types import AgentDef

PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"

# M3 只读工具白名单(全部 READ-ONLY; 写工具 M4 才开放)
CHIEF_TOOLS = [
    "project_info",
    "list_chapters",
    "read_outline",
    "read_character",
    "search_memory",
]


def _load_prompt(name: str) -> str:
    return (PROMPTS_DIR / name).read_text(encoding="utf-8")


def chief_agent_def() -> AgentDef:
    """主编定义(模型角色: settings.models.chief, 缺省回退 default_model)。"""
    return AgentDef(
        id="chief",
        name="主编",
        system_prompt=_load_prompt("chief_system.md"),
        tools=list(CHIEF_TOOLS),
        max_tool_rounds=4,
        model_role="chief",
    )
