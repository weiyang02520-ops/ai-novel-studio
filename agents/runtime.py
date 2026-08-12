"""Agent Runtime — 通用 tool-call loop + 内存会话(Core, 无 UI 依赖)。

- 只依赖 BaseProvider + ChatMessage/ChatResult/ToolCall(禁止 import httpx/OpenAICompatibleProvider)
- 消息顺序固定: system(Chief Prompt) → 会话消息 → assistant/tool loop(§23)
- assistant tool-call message 必须保留(§25); tool result 带 tool_call_id 回填(§26)
- 多工具调用保持模型顺序执行(§27)
- tool 总量限制(整个 turn): 整批 preflight(§30), 超限 0 执行
- tool round 上限(§28); runaway 必然终止(§31)
- ProviderError → AgentRunResult(status=provider_error, safe code), 不 traceback(§32)
- weak model(tool_calls=False): 不发送 tools, 首轮注入 bounded context pack(§80-86)
- 会话仅内存(§88): 保留 system + 最近消息; 旧 tool 结果优先裁剪(§91-92)
"""
from __future__ import annotations

import time
from typing import Any, Optional

from agents.context import build_fallback_context
from agents.types import (
    AgentCallRecord,
    AgentContext,
    AgentRunResult,
    AgentToolCallRecord,
)
from llm.provider import ProviderError
from llm.types import ChatMessage

# 会话消息上限(不含 system; 旧消息优先裁剪, tool 结果先被剪掉)(§91-92)
SESSION_MAX_MESSAGES = 40


def _ms(t0: float) -> float:
    return round((time.monotonic() - t0) * 1000, 1)


class AgentSession:
    """轻量内存会话(不落盘; 进程结束即消失)。"""

    def __init__(self, context: AgentContext):
        self.context = context
        self.messages: list[ChatMessage] = []  # 不含 system(动态注入)
        self._pack_injected = False

    def ask(self, user_text: str) -> AgentRunResult:
        return run_agent(self, user_text)


def _trim_session(session: AgentSession) -> None:
    """超限裁剪: 保留开头(首轮 pack/问题)与最近消息, 优先剪中间旧 tool 结果。"""
    msgs = session.messages
    if len(msgs) <= SESSION_MAX_MESSAGES:
        return
    # 保留前 2 条(通常 = 首轮用户问题 / 上下文 pack)与最近 N-2 条
    keep_head = 2
    session.messages = msgs[:keep_head] + msgs[-(SESSION_MAX_MESSAGES - keep_head):]


def run_agent(session: AgentSession, user_text: str) -> AgentRunResult:
    ctx = session.context
    agent = ctx.agent_def
    registry = ctx.tool_registry
    provider = ctx.provider
    system = ChatMessage(role="system", content=agent.system_prompt)

    result = AgentRunResult()

    session.messages.append(ChatMessage(role="user", content=user_text))
    _trim_session(session)

    # 弱模型: 首轮注入 bounded context pack, 不发送 tools(§80-86)
    tools: Optional[list[dict[str, Any]]] = None
    if provider.config.tool_calls:
        tools = registry.schemas_for(agent.tools)
    elif not session._pack_injected:
        session.messages.insert(0, ChatMessage(role="user", content=build_fallback_context(ctx)))
        session._pack_injected = True

    rounds = 0
    while True:
        started = time.monotonic()
        try:
            resp = provider.chat([system] + session.messages, tools=tools)
        except ProviderError as e:
            result.status = "provider_error"
            result.error_code = e.code
            result.error_message = e.message
            result.calls.append(AgentCallRecord(round=rounds + 1, duration_ms=_ms(started)))
            return result
        result.calls.append(AgentCallRecord(
            round=rounds + 1, model=resp.model, usage=resp.usage,
            duration_ms=_ms(started)))

        if not resp.tool_calls:
            session.messages.append(ChatMessage(role="assistant", content=resp.text))
            _trim_session(session)
            result.text = resp.text
            result.status = "completed"
            result.rounds = rounds
            return result

        # 工具轮次上限: 达到后整批拒绝, 不再请求(§28, 121)
        if rounds >= agent.max_tool_rounds:
            result.status = "round_limit_exceeded"
            result.rounds = rounds
            return result

        # tool 总量 preflight(§30): 剩余 quota 不足 → 整批拒绝, 0 执行
        remaining = ctx.max_tool_calls - result.tool_calls_count
        if len(resp.tool_calls) > remaining:
            result.status = "tool_limit_exceeded"
            result.rounds = rounds
            return result

        rounds += 1
        # assistant tool-call message 必须保留(§25)
        session.messages.append(ChatMessage(role="assistant", content="", tool_calls=resp.tool_calls))

        # 执行全部工具(保持模型顺序 §27)
        for call in resp.tool_calls:
            t0 = time.monotonic()
            output, trace = registry.execute(agent, call.name, call.arguments_json, ctx)
            trace.duration_ms = _ms(t0)
            result.tool_trace.append(trace)
            result.tool_calls_count += 1
            # tool result 回填(§26: tool_call_id 与 call.id 对应)
            session.messages.append(ChatMessage(role="tool", content=output, tool_call_id=call.id))

        _trim_session(session)
