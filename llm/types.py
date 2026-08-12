"""Provider 内部统一数据模型(Provider 无关)。

M3 Agent Runtime 只依赖这里的类型, 不依赖 httpx/urllib/OpenAI JSON dict。
"""
from __future__ import annotations

import dataclasses
from typing import Any, Optional


@dataclasses.dataclass
class ToolCall:
    """工具调用(仅解析, M2 不执行)。arguments_json 保留原始 JSON 字符串。"""

    id: str
    name: str
    arguments_json: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": "function",
            "function": {"name": self.name, "arguments": self.arguments_json},
        }


@dataclasses.dataclass
class ChatMessage:
    """对话消息。role: system | user | assistant | tool。"""

    role: str
    content: str = ""
    tool_calls: Optional[list[ToolCall]] = None
    tool_call_id: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"role": self.role, "content": self.content}
        if self.tool_calls is not None:
            d["tool_calls"] = [tc.to_dict() for tc in self.tool_calls]
        if self.tool_call_id is not None:
            d["tool_call_id"] = self.tool_call_id
        return d


@dataclasses.dataclass
class Usage:
    """Token 用量。estimated=True 时是本地近似, 不是服务端精确值。"""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    estimated: bool = False
    source: str = "provider"  # provider | estimated

    @classmethod
    def estimated_usage(cls, prompt_tokens: int, completion_tokens: int) -> "Usage":
        total = prompt_tokens + completion_tokens
        return cls(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total,
            estimated=True,
            source="estimated",
        )

    def is_empty(self) -> bool:
        return self.prompt_tokens == 0 and self.completion_tokens == 0 and self.total_tokens == 0


@dataclasses.dataclass
class ChatResult:
    """非流式响应(成功)。不携带裸 HTTP response。"""

    text: str
    tool_calls: Optional[list[ToolCall]] = None
    usage: Optional[Usage] = None
    model: str = ""
    finish_reason: str = "stop"


@dataclasses.dataclass
class ChatChunk:
    """流式事件(增量)。

    kind:
      text       — text 增量
      tool_call  — 工具调用增量(index/id/name/arguments_delta; 各字段只含本块的 delta, 非累计值)
      finish     — finish_reason
      usage      — 服务端最终 usage(可能没有)

    tool_call_arguments_delta 是增量: 消费方按 index 自行拼接(或用 ToolCallAccumulator)。
    """

    kind: str = "text"
    text: str = ""
    tool_call_index: Optional[int] = None
    tool_call_id: Optional[str] = None
    tool_call_name: Optional[str] = None
    tool_call_arguments_delta: Optional[str] = None
    finish_reason: str = ""
    usage: Optional[Usage] = None
