"""Agent 内部类型(Core, 无 UI 依赖)。

- AgentDef / AgentContext / AgentRunResult / AgentToolCallRecord / AgentCallRecord
- Runtime 只返回结构化结果, 不 print / 不依赖 CLI
"""
from __future__ import annotations

import dataclasses
from typing import Any, Optional

from core.config import Settings
from core.project import Project
from llm.provider import BaseProvider
from llm.types import Usage


@dataclasses.dataclass
class AgentDef:
    """Agent 定义(通用; M3 只实例化 Chief, Writer/Reviewer 未来再建)。"""

    id: str
    name: str
    system_prompt: str
    tools: list[str]           # 工具白名单
    max_tool_rounds: int = 4   # 每轮对话最多工具轮次
    model_role: Optional[str] = None  # settings.models.<role>; None → default_model


@dataclasses.dataclass
class AgentContext:
    """运行时上下文(project/settings/provider/registry/agent 注入)。"""

    project: Project
    settings: Settings
    provider: BaseProvider
    tool_registry: Any
    agent_def: AgentDef
    max_tool_calls: int = 8   # 整个用户 turn 的工具调用总量上限(settings.workflow.max_tool_calls_per_turn)


@dataclasses.dataclass
class AgentToolCallRecord:
    """工具 trace(只记元数据; 不保存 arguments/完整 result/正文)。"""

    name: str
    success: bool = False
    error: Optional[str] = None
    duration_ms: Optional[float] = None
    output_length: Optional[int] = None
    mutates_project: bool = False
    added_lines: Optional[int] = None
    removed_lines: Optional[int] = None
    diff_preview: Optional[str] = None


@dataclasses.dataclass
class AgentCallRecord:
    """每次 LLM 调用记录(内存 usage trace; CLI 负责写入 UsageService)。"""

    round: int
    model: str = ""
    usage: Optional[Usage] = None
    duration_ms: Optional[float] = None
    stream: bool = False


@dataclasses.dataclass
class AgentRunResult:
    """Agent 一次运行的结构化结果。"""

    text: str = ""
    status: str = "completed"  # completed | tool_limit_exceeded | round_limit_exceeded | provider_error
    rounds: int = 0
    tool_calls_count: int = 0
    tool_trace: list[AgentToolCallRecord] = dataclasses.field(default_factory=list)
    calls: list[AgentCallRecord] = dataclasses.field(default_factory=list)
    error_code: Optional[str] = None
    error_message: Optional[str] = None


class AgentError(Exception):
    """Agent 层错误(面向用户的安全消息, 不含 Key/绝对路径)。"""

    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)
