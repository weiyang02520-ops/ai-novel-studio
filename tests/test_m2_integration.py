"""M2 集成测试: 真实 HttpTransport → localhost mock server(生产 path, 不访问互联网)。"""
from __future__ import annotations

import pytest

from core.config import ModelConfig
from llm.openai_compatible import OpenAICompatibleProvider
from llm.provider import (
    AUTH_ERROR,
    EMPTY_RESPONSE,
    MALFORMED_RESPONSE,
    RATE_LIMIT,
    SERVER_ERROR,
    STREAM_INTERRUPTED,
    ProviderError,
)
from llm.types import ChatMessage
from conftest import FakeSecretStore, fake_key


def _cfg(base_url: str, **kw) -> ModelConfig:
    base = dict(model="m1")
    base.update(kw)
    return ModelConfig(base_url=base_url, **base)


def _prov(base_url: str, *, secret_reference: str = "", store=None):
    return OpenAICompatibleProvider(_cfg(base_url, secret_reference=secret_reference),
                                    secret_store=store if secret_reference else None)


# ── non-stream 生产 path(§94, 124) ────────────────────────

def test_non_stream_integration(server):
    p = _prov(server.base_url)
    result = p.chat([ChatMessage(role="user", content="你好")])
    assert result.text == "你好"
    assert result.usage is not None and result.usage.total_tokens == 12

    req = server.requests[0]
    assert req["method"] == "POST"
    assert req["path"] == "/v1/chat/completions"
    assert req["headers"].get("Content-Type", "").startswith("application/json")
    assert req["body_json"]["model"] == "m1"
    assert req["body_json"]["messages"][0]["content"] == "你好"
    assert "stream" not in req["body_json"]


def test_auth_header_integration(server):
    key = fake_key()
    server.require_auth = key
    p = _prov(server.base_url, secret_reference="r", store=FakeSecretStore({"r": key}))
    result = p.chat([ChatMessage(role="user", content="hi")])
    assert result.text == "你好"
    assert server.last_auth() == f"Bearer {key}"


def test_keyless_no_authorization_integration(server):
    server.forbid_auth = True
    p = _prov(server.base_url, secret_reference="")
    result = p.chat([ChatMessage(role="user", content="hi")])
    assert result.text == "你好"
    assert server.auth_violation is False


def test_trailing_slash_integration(server):
    p = _prov(server.base_url + "/", secret_reference="")
    p.chat([ChatMessage(role="user", content="hi")])
    assert server.requests[0]["path"] == "/v1/chat/completions"  # 无 //


# ── stream 生产 path(§95) ─────────────────────────────────

def test_stream_integration(server):
    server.stream_mode = "full"
    server.stream_chunks = [
        '{"choices":[{"delta":{"content":"山"}}]}',
        '{"choices":[{"delta":{"content":"河"}}]}',
        '{"choices":[{"delta":{"content":"不记"}}]}',
        '{"choices":[{"delta":{},"finish_reason":"stop"}]}',
        "[DONE]",
    ]
    p = _prov(server.base_url, secret_reference="")
    chunks = list(p.stream_chat([ChatMessage(role="user", content="hi")]))
    text = "".join(c.text for c in chunks if c.kind == "text")
    assert text == "山河不记"
    assert server.requests[0]["body_json"]["stream"] is True


# ── 错误状态生产 path(§98) ────────────────────────────────

def test_401_integration(server):
    server.responses.append((401, {"error": {"message": "bad key"}}))
    p = _prov(server.base_url, secret_reference="")
    with pytest.raises(ProviderError) as e:
        p.chat([ChatMessage(role="user", content="hi")])
    assert e.value.code == AUTH_ERROR


def test_429_integration(server):
    server.responses.append((429, {"error": {"message": "slow down"}}))
    p = _prov(server.base_url, secret_reference="")
    with pytest.raises(ProviderError) as e:
        p.chat([ChatMessage(role="user", content="hi")])
    assert e.value.code == RATE_LIMIT
    assert server.request_count() == 1


def test_retry_503_then_200_integration(server):
    server.responses.append((503, {"error": {"message": "overloaded"}}))
    p = _prov(server.base_url, secret_reference="")
    result = p.chat([ChatMessage(role="user", content="hi")])
    assert result.text == "你好"
    assert server.request_count() == 2


def test_empty_content_integration(server):
    server.responses.append((200, {"choices": [{"message": {"role": "assistant", "content": ""},
                                                "finish_reason": "stop"}]}))
    p = _prov(server.base_url, secret_reference="")
    with pytest.raises(ProviderError) as e:
        p.chat([ChatMessage(role="user", content="hi")])
    assert e.value.code == EMPTY_RESPONSE


def test_html_error_no_traceback_integration(server):
    server.responses.append((502, "<html><body>Bad Gateway</body></html>"))
    server.responses.append((502, "<html><body>Bad Gateway</body></html>"))  # 502 retryable → 重试后仍失败
    p = _prov(server.base_url, secret_reference="")
    with pytest.raises(ProviderError) as e:
        p.chat([ChatMessage(role="user", content="hi")])
    assert e.value.code == SERVER_ERROR
    assert "HTML" in e.value.message  # 安全消息, 不 dump HTML


# ── stream 中断生产 path(§100) ────────────────────────────

def test_stream_interrupt_integration(server):
    """已产出部分内容后连接断开 → STREAM_INTERRUPTED, 不从头重试(请求数 1)。"""
    server.stream_mode = "interrupt"
    server.stream_chunks = [
        '{"choices":[{"delta":{"content":"Hello"}}]}',
    ]
    p = _prov(server.base_url, secret_reference="")
    out: list[str] = []
    with pytest.raises(ProviderError) as e:
        for chunk in p.stream_chat([ChatMessage(role="user", content="hi")]):
            out.append(chunk.text)
    assert e.value.code == STREAM_INTERRUPTED
    assert "".join(out) == "Hello"
    assert server.request_count() == 1


def test_stream_200_non_sse_integration(server):
    """stream=true 但服务返回普通 JSON → MALFORMED_RESPONSE(§46)。"""
    server.stream_mode = None  # 默认返回 JSON(非 SSE)
    p = _prov(server.base_url, secret_reference="")
    with pytest.raises(ProviderError) as e:
        list(p.stream_chat([ChatMessage(role="user", content="hi")]))
    assert e.value.code == MALFORMED_RESPONSE


# ── usage missing 生产 path ───────────────────────────────

def test_usage_missing_estimated_integration(server):
    server.responses.append((200, {"choices": [{"message": {"role": "assistant", "content": "ok"},
                                                "finish_reason": "stop"}]}))
    p = _prov(server.base_url, secret_reference="")
    result = p.chat([ChatMessage(role="user", content="hi")])
    assert result.usage.estimated is True
