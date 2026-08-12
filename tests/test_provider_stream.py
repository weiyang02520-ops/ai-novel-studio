"""Provider 流式测试(FakeTransport 注入 SSE 行)。"""
from __future__ import annotations

import pytest

from core.config import ModelConfig
from llm.openai_compatible import OpenAICompatibleProvider
from llm.provider import (
    AUTH_ERROR,
    MALFORMED_RESPONSE,
    NETWORK_ERROR,
    STREAM_INTERRUPTED,
    ProviderError,
)
from llm.testing import FakeTransport
from llm.transport import TransportError, TransportHTTPError
from llm.types import ChatChunk, ChatMessage
from conftest import FakeSecretStore, fake_key


def _cfg(**kw) -> ModelConfig:
    base = dict(base_url="https://example.com/v1", model="m1")
    base.update(kw)
    return ModelConfig(**base)


def _prov(cfg=None, store=None, transport=None):
    return OpenAICompatibleProvider(cfg or _cfg(), store, transport or FakeTransport())


def _collect(p, messages=None):
    chunks = list(p.stream_chat(messages or [ChatMessage(role="user", content="hi")]))
    text = "".join(c.text for c in chunks if c.kind == "text")
    finish = next((c.finish_reason for c in chunks if c.kind == "finish"), "")
    usage = next((c.usage for c in chunks if c.kind == "usage"), None)
    tool_calls = [c for c in chunks if c.kind == "tool_call"]
    return text, finish, usage, tool_calls


# ── 基本 SSE 解析(§40-41, 48) ────────────────────────────

def test_stream_text_deltas_concatenated():
    ft = FakeTransport()
    ft.stream_lines = [
        'data: {"choices":[{"delta":{"content":"山"}}]}',
        'data: {"choices":[{"delta":{"content":"河"}}]}',
        'data: {"choices":[{"delta":{"content":"不记"}}]}',
        'data: {"choices":[{"delta":{"content":"🌙"}}]}',
        'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}',
        "data: [DONE]",
    ]
    text, finish, _, _ = _collect(_prov(transport=ft))
    assert text == "山河不记🌙"
    assert finish == "stop"


def test_stream_ignores_blank_and_comment_lines():
    ft = FakeTransport()
    ft.stream_lines = [
        "",
        ": keep-alive comment",
        'data: {"choices":[{"delta":{"content":"A"}}]}',
        " ",
        'data: {"choices":[{"delta":{"content":"B"}}]}',
        "data: [DONE]",
    ]
    text, _, _, _ = _collect(_prov(transport=ft))
    assert text == "AB"


def test_stream_payload_has_stream_true():
    ft = FakeTransport()
    ft.stream_lines = ["data: [DONE]"]
    list(_prov(transport=ft).stream_chat([ChatMessage(role="user", content="hi")]))
    assert ft.requests[0]["payload"]["stream"] is True


def test_stream_missing_finish_ok():
    """finish_reason 缺失/未知不崩溃(§43)。"""
    ft = FakeTransport()
    ft.stream_lines = [
        'data: {"choices":[{"delta":{"content":"ok"}}]}',
        'data: {"choices":[{"delta":{},"finish_reason":"weird_reason"}]}',
        "data: [DONE]",
    ]
    text, finish, _, _ = _collect(_prov(transport=ft))
    assert text == "ok"
    assert finish == "weird_reason"


# ── 流式 usage(§44) ──────────────────────────────────────

def test_stream_usage_captured():
    ft = FakeTransport()
    ft.stream_lines = [
        'data: {"choices":[{"delta":{"content":"hi"}}]}',
        'data: {"choices":[],"usage":{"prompt_tokens":3,"completion_tokens":2,"total_tokens":5}}',
        "data: [DONE]",
    ]
    _, _, usage, _ = _collect(_prov(transport=ft))
    assert usage is not None and usage.total_tokens == 5 and not usage.estimated


def test_stream_without_usage_no_crash():
    ft = FakeTransport()
    ft.stream_lines = [
        'data: {"choices":[{"delta":{"content":"hi"}}]}',
        "data: [DONE]",
    ]
    _, _, usage, _ = _collect(_prov(transport=ft))
    assert usage is None  # 调用方(CLI)负责 estimated 兜底


# ── 流式 tool_calls delta 契约(§5-7) ─────────────────────

def test_stream_tool_call_arguments_are_deltas_not_cumulative():
    """Consumer 方式: 每块输出自己的 delta; 拼接后得到完整 arguments。"""
    from llm.provider import ToolCallAccumulator
    ft = FakeTransport()
    ft.stream_lines = [
        'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"call_1","function":{"name":"f1","arguments":""}}]}}]}',
        'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"arguments":"{\\"chap"}}]}}]}',
        'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"arguments":"ter\\":"}}]}}]}',
        'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"arguments":"1}"}}]}}]}',
        'data: {"choices":[{"delta":{},"finish_reason":"tool_calls"}]}',
        "data: [DONE]",
    ]
    chunks = list(_prov(transport=ft).stream_chat([ChatMessage(role="user", content="hi")]))

    # delta 非累计: 第 2/3/4 块只含各自增量
    deltas = [c.tool_call_arguments_delta or "" for c in chunks if c.kind == "tool_call"]
    assert deltas == ["", '{"chap', 'ter":', "1}"], f"必须是 delta 而非累计值: {deltas}"

    # consumer 方式拼接
    args = ""
    for c in chunks:
        if c.kind == "tool_call":
            args += c.tool_call_arguments_delta or ""
    assert args == '{"chapter":1}'

    # id/name 只出现一次(不会因累计重复)
    ids = [c.tool_call_id for c in chunks if c.kind == "tool_call" and c.tool_call_id]
    names = [c.tool_call_name for c in chunks if c.kind == "tool_call" and c.tool_call_name]
    assert ids == ["call_1"]
    assert names == ["f1"]

    # ToolCallAccumulator 聚合契约
    acc = ToolCallAccumulator()
    for c in chunks:
        acc.add(c)
    calls = acc.tool_calls()
    assert len(calls) == 1
    assert calls[0].id == "call_1"
    assert calls[0].name == "f1"
    assert calls[0].arguments_json == '{"chapter":1}'


def test_stream_tool_call_multiple_indexes_independent():
    """多 index(0/1)各自独立聚合, delta 互不串扰。"""
    from llm.provider import ToolCallAccumulator
    ft = FakeTransport()
    ft.stream_lines = [
        'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"a","function":{"name":"f_a","arguments":"{\\"x\\":"}}]}}]}',
        'data: {"choices":[{"delta":{"tool_calls":[{"index":1,"id":"b","function":{"name":"f_b","arguments":"{\\"y\\":"}}]}}]}',
        'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"arguments":"1}"}},{"index":1,"function":{"arguments":"2}"}}]}}]}',
        'data: {"choices":[{"delta":{},"finish_reason":"tool_calls"}]}',
        "data: [DONE]",
    ]
    chunks = list(_prov(transport=ft).stream_chat([ChatMessage(role="user", content="hi")]))

    acc = ToolCallAccumulator()
    for c in chunks:
        acc.add(c)
    calls = acc.tool_calls()
    assert len(calls) == 2
    assert calls[0].id == "a" and calls[0].name == "f_a" and calls[0].arguments_json == '{"x":1}'
    assert calls[1].id == "b" and calls[1].name == "f_b" and calls[1].arguments_json == '{"y":2}'


def test_tool_call_accumulator_clear():
    from llm.provider import ToolCallAccumulator
    acc = ToolCallAccumulator()
    acc.add(ChatChunk(kind="tool_call", tool_call_index=0, tool_call_id="c1",
                      tool_call_name="f", tool_call_arguments_delta="{}"))
    assert len(acc.tool_calls()) == 1
    acc.clear()
    assert acc.tool_calls() == []


def test_tool_call_accumulator_ignores_non_tool_chunks():
    from llm.provider import ToolCallAccumulator
    acc = ToolCallAccumulator()
    acc.add(ChatChunk(kind="text", text="hi"))
    acc.add(ChatChunk(kind="finish", finish_reason="stop"))
    assert acc.tool_calls() == []


# ── 畸形 SSE(§47) ────────────────────────────────────────

def test_stream_bad_json_data_malformed():
    ft = FakeTransport()
    ft.stream_lines = ['data: {broken json']
    with pytest.raises(ProviderError) as e:
        _collect(_prov(transport=ft))
    assert e.value.code == MALFORMED_RESPONSE


def test_stream_non_object_data_malformed():
    ft = FakeTransport()
    ft.stream_lines = ['data: [1,2,3]']
    with pytest.raises(ProviderError) as e:
        _collect(_prov(transport=ft))
    assert e.value.code == MALFORMED_RESPONSE


# ── 中断 / 重试(§36, 100) ────────────────────────────────

def test_stream_interrupted_after_emission_no_retry():
    """已产出部分内容后连接断开 → STREAM_INTERRUPTED, 请求数 = 1(不从头重试)。"""
    from llm.transport import SSEStream
    ft = FakeTransport()
    state = {"calls": 0}

    def flaky_stream(url, headers, payload):
        state["calls"] += 1

        def gen():
            yield 'data: {"choices":[{"delta":{"content":"Hello"}}]}'
            raise TransportError("interrupted", "connection lost")

        return SSEStream(gen(), close_fn=lambda: None)

    ft.stream_sse = flaky_stream
    p = _prov(transport=ft)

    out: list[str] = []
    with pytest.raises(ProviderError) as e:
        for chunk in p.stream_chat([ChatMessage(role="user", content="hi")]):
            out.append(chunk.text)
    assert e.value.code == STREAM_INTERRUPTED
    assert "".join(out) == "Hello"  # 已输出部分保留
    assert state["calls"] == 1  # 不重试


def test_stream_network_fail_before_emission_retries():
    """未产出任何内容 → 可安全重试 1 次。"""
    ft = FakeTransport()
    ft.stream_lines = ['data: {"choices":[{"delta":{"content":"ok"}}]}', "data: [DONE]"]
    p = _prov(transport=ft)
    state = {"count": 0}
    real_stream = ft.stream_sse

    def flaky(url, headers, payload):
        state["count"] += 1
        if state["count"] == 1:
            raise TransportError("network", "down")
        ft.stream_transport_error = None  # 第二次成功
        return real_stream(url, headers, payload)

    ft.stream_sse = flaky
    text, _, _, _ = _collect(p)
    assert text == "ok"
    assert state["count"] == 2  # 重试 1 次


def test_stream_timeout_before_emission_retries():
    state = {"count": 0}
    ft = FakeTransport()
    ft.stream_lines = ['data: {"choices":[{"delta":{"content":"ok"}}]}', "data: [DONE]"]
    real_stream = ft.stream_sse

    def flaky(url, headers, payload):
        state["count"] += 1
        if state["count"] == 1:
            raise TransportError("timeout", "t")
        ft.stream_transport_error = None
        return real_stream(url, headers, payload)

    ft.stream_sse = flaky
    text, _, _, _ = _collect(_prov(transport=ft))
    assert text == "ok"
    assert state["count"] == 2


def test_stream_401_no_retry():
    ft = FakeTransport()
    ft.stream_http_error = TransportHTTPError(401, b'{"error":{"message":"bad key"}}', "application/json")
    with pytest.raises(ProviderError) as e:
        _collect(_prov(transport=ft))
    assert e.value.code == AUTH_ERROR
    assert ft.request_count() == 1


def test_stream_503_before_emission_retries():
    state = {"count": 0}
    ft = FakeTransport()
    ft.stream_lines = ['data: {"choices":[{"delta":{"content":"ok"}}]}', "data: [DONE]"]
    real_stream = ft.stream_sse

    def flaky(url, headers, payload):
        state["count"] += 1
        if state["count"] == 1:
            raise TransportHTTPError(503, b'{"error":{"message":"overloaded"}}', "application/json")
        ft.stream_http_error = None
        return real_stream(url, headers, payload)

    ft.stream_sse = flaky
    text, _, _, _ = _collect(_prov(transport=ft))
    assert text == "ok"
    assert state["count"] == 2


def test_stream_200_non_sse_malformed():
    ft = FakeTransport()
    ft.stream_http_error = TransportHTTPError(200, b'{"choices":[{"message":{"role":"assistant","content":"x"}}]}', "application/json")
    with pytest.raises(ProviderError) as e:
        _collect(_prov(transport=ft))
    assert e.value.code == MALFORMED_RESPONSE


def test_stream_network_exhausted():
    ft = FakeTransport()
    ft.stream_transport_error = TransportError("network", "down")
    with pytest.raises(ProviderError) as e:
        _collect(_prov(transport=ft))
    assert e.value.code == NETWORK_ERROR
    assert ft.request_count() == 2
