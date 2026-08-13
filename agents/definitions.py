"""Agent definitions: Chief knowledge work plus the M5 prose-only Writer."""
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

M4_CHIEF_TOOLS = CHIEF_TOOLS + [
    "read_world", "read_rules", "read_memory", "search_project_knowledge", "inspect_knowledge_status",
    "update_outline", "update_character", "update_world", "save_memory_entry",
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


def m4_chief_agent_def() -> AgentDef:
    """M4 Chief: knowledge reads/writes, still no chapter mutation or Writer."""
    agent = chief_agent_def()
    agent.tools = list(M4_CHIEF_TOOLS)
    agent.max_tool_rounds = 6
    return agent

def writer_agent_def() -> AgentDef:
    """M5 Writer has no tools; workflow owns every persistent draft mutation."""
    return AgentDef(id="writer", name="写作分身", system_prompt=_load_prompt("writer_system.md"),
                    tools=[], max_tool_rounds=0, model_role="writer")


def reviewer_agent_def() -> AgentDef:
    """M6 Reviewer is read/analyze-only; workflow owns every mutation."""
    return AgentDef(id="reviewer", name="审稿", system_prompt=_load_prompt("reviewer_system.md"),
                    tools=[], max_tool_rounds=0, model_role="reviewer")
