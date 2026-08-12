"""Provider 非流式测试(FakeTransport 注入, 不联网)。"""
from __future__ import annotations

import pytest

from core.config import ModelConfig
from llm.openai_compatible import OpenAICompatibleProvider
from llm.provider import (
    AUTH_ERROR,
    BAD_REQUEST,
    CONFIG_ERROR,
    CONTEXT_TOO_LONG,
    EMPTY_RESPONSE,
    ENDPOINT_ERROR,
    HTTP_ERROR,
    KEY_NOT_CONFIGURED,
    MALFORMED_RESPONSE,
    MODEL_NOT_FOUND,
    NETWORK_ERROR,
    NOT_FOUND,
    PERMISSION_ERROR,
    RATE_LIMIT,
    SERVER_ERROR,
    TIMEOUT,
    ProviderError,
)
from llm.testing import FakeTransport
from llm.transport import TransportError
from llm.types import ChatMessage, ToolCall
from conftest import FakeSecretStore, fake_key


def _cfg(**kw) -> ModelConfig:
    base = dict(base_url="https://example.com/v1", model="m1")
    base.update(kw)
    return ModelConfig(**base)


def _prov(cfg: ModelConfig | None = None, store=None, transport: FakeTransport | None = None):
    return OpenAICompatibleProvider(cfg or _cfg(), store, transport or FakeTransport())


OK_BODY = {
    "id": "x", "object": "chat.completion", "model": "mock-model",
    "choices": [{"index": 0, "message": {"role": "assistant", "content": "你好"},
                 "finish_reason": "stop"}],
    "usage": {"prompt_tokens": 5, "completion_tokens": 7, "total_tokens": 12},
}


# ── 成功解析 ─────────────────────────────────────────────

def test_chat_success_parses():
    ft = FakeTransport()
    ft.add_response(200, OK_BODY)
    p = _prov(transport=ft)
    result = p.chat([ChatMessage(role="user", content="hi")])
    assert result.text == "你好"
    assert result.model == "mock-model"
    assert result.finish_reason == "stop"
    assert result.usage is not None and not result.usage.estimated
    assert result.usage.total_tokens == 12


def test_chat_payload_correct():
    ft = FakeTransport()
    ft.add_response(200, OK_BODY)
    p = _prov(transport=ft)
    p.chat([ChatMessage(role="user", content="hi")], temperature=0.3)
    req = ft.requests[0]
    payload = req["payload"]
    assert payload["model"] == "m1"
    assert payload["messages"] == [{"role": "user", "content": "hi"}]
    assert payload["temperature"] == 0.3
    assert "stream" not in payload
    assert "tools" not in payload  # 未传 tools 不发送
    assert req["url"] == "https://example.com/v1/chat/completions"


def test_chat_uses_config_temperature_by_default():
    ft = FakeTransport()
    ft.add_response(200, OK_BODY)
    p = _prov(_cfg(temperature=0.5), transport=ft)
    p.chat([ChatMessage(role="user", content="hi")])
    assert ft.requests[0]["payload"]["temperature"] == 0.5


# ── usage 兼容(§82) ───────────────────────────────────────

def test_usage_missing_falls_back_estimated():
    body = {"choices": [{"message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}]}
    ft = FakeTransport()
    ft.add_response(200, body)
    result = _prov(transport=ft).chat([ChatMessage(role="user", content="hi")])
    assert result.usage is not None
    assert result.usage.estimated is True
    assert result.usage.source == "estimated"
    assert result.usage.total_tokens == result.usage.prompt_tokens + result.usage.completion_tokens


def test_usage_total_missing_summed():
    body = {"choices": [{"message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 3, "completion_tokens": 4}}
    ft = FakeTransport()
    ft.add_response(200, body)
    result = _prov(transport=ft).chat([ChatMessage(role="user", content="hi")])
    assert result.usage.total_tokens == 7


def test_usage_invalid_types_malformed():
    body = {"choices": [{"message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": "x", "completion_tokens": 4}}
    ft = FakeTransport()
    ft.add_response(200, body)
    with pytest.raises(ProviderError) as e:
        _prov(transport=ft).chat([ChatMessage(role="user", content="hi")])
    assert e.value.code == MALFORMED_RESPONSE


# ── 空响应 / 畸形响应(§37, 80, 101) ──────────────────────

def test_empty_content_with_tool_calls_ok():
    body = {"choices": [{"message": {"role": "assistant", "content": "",
                                     "tool_calls": [{"id": "c1", "type": "function",
                                                     "function": {"name": "get_weather", "arguments": "{}"}}]},
                         "finish_reason": "tool_calls"}]}
    ft = FakeTransport()
    ft.add_response(200, body)
    result = _prov(transport=ft).chat([ChatMessage(role="user", content="hi")])
    assert result.text == ""
    assert result.tool_calls is not None and result.tool_calls[0].name == "get_weather"
    assert result.tool_calls[0].arguments_json == "{}"


def test_empty_response_rejected():
    body = {"choices": [{"message": {"role": "assistant", "content": ""}, "finish_reason": "stop"}]}
    ft = FakeTransport()
    ft.add_response(200, body)
    with pytest.raises(ProviderError) as e:
        _prov(transport=ft).chat([ChatMessage(role="user", content="hi")])
    assert e.value.code == EMPTY_RESPONSE


@pytest.mark.parametrize("bad_body", [
    "not json",           # 200 + 非法 JSON
    [],                   # 200 + []
    {},                   # 200 + 无 choices
    {"choices": []},      # choices=[]
    {"choices": [{}]},    # 无 message
    {"choices": [{"message": "x"}]},  # message 非 dict
    {"choices": [{"message": {"role": "assistant", "content": 123}}]},  # content 非 str
])
def test_malformed_responses(bad_body):
    ft = FakeTransport()
    if isinstance(bad_body, str):
        ft.add_response(200, bad_body)
    else:
        ft.add_response(200, bad_body)
    with pytest.raises(ProviderError) as e:
        _prov(transport=ft).chat([ChatMessage(role="user", content="hi")])
    assert e.value.code == MALFORMED_RESPONSE


# ── 状态映射(§26-33, 75-79) ──────────────────────────────

@pytest.mark.parametrize("status,expected_code,expected_retryable", [
    (400, BAD_REQUEST, False),
    (401, AUTH_ERROR, False),
    (403, PERMISSION_ERROR, False),
    (404, NOT_FOUND, False),
    (405, ENDPOINT_ERROR, False),
    (408, TIMEOUT, True),
    (409, BAD_REQUEST, False),
    (422, BAD_REQUEST, False),
    (429, RATE_LIMIT, False),
    (500, SERVER_ERROR, True),
    (502, SERVER_ERROR, True),
    (503, SERVER_ERROR, True),
    (504, SERVER_ERROR, True),
    (418, HTTP_ERROR, False),
])
def test_status_mapping(status, expected_code, expected_retryable):
    ft = FakeTransport()
    ft.add_response(status, {"error": {"message": "some error"}})
    if expected_retryable:
        ft.add_response(status, {"error": {"message": "some error"}})  # 重试后仍失败
    with pytest.raises(ProviderError) as e:
        _prov(transport=ft).chat([ChatMessage(role="user", content="hi")])
    assert e.value.code == expected_code
    assert e.value.retryable == expected_retryable
    assert e.value.status_code == status


def test_400_model_not_found():
    ft = FakeTransport()
    ft.add_response(400, {"error": {"message": "model 'x' does not exist"}})
    with pytest.raises(ProviderError) as e:
        _prov(transport=ft).chat([ChatMessage(role="user", content="hi")])
    assert e.value.code == MODEL_NOT_FOUND


def test_400_context_too_long():
    ft = FakeTransport()
    ft.add_response(400, {"error": {"message": "This model's maximum context length is 8192 tokens"}})
    with pytest.raises(ProviderError) as e:
        _prov(transport=ft).chat([ChatMessage(role="user", content="hi")])
    assert e.value.code == CONTEXT_TOO_LONG


def test_429_with_retry_after_hint():
    ft = FakeTransport()
    ft.add_response(429, {"error": {"message": "rate limited"}}, headers={"Retry-After": "17"})
    with pytest.raises(ProviderError) as e:
        _prov(transport=ft).chat([ChatMessage(role="user", content="hi")])
    assert e.value.code == RATE_LIMIT
    assert "17" in e.value.message


# ── retry(§35, 83) ───────────────────────────────────────

def test_retry_503_then_200():
    ft = FakeTransport()
    ft.add_response(503, {"error": {"message": "overloaded"}})
    ft.add_response(200, OK_BODY)
    result = _prov(transport=ft).chat([ChatMessage(role="user", content="hi")])
    assert result.text == "你好"
    assert ft.request_count() == 2


def test_retry_timeout_then_200():
    ft = FakeTransport()
    ft.add_exception(TransportError("timeout", "t"))
    ft.add_response(200, OK_BODY)
    result = _prov(transport=ft).chat([ChatMessage(role="user", content="hi")])
    assert ft.request_count() == 2


def test_retry_network_then_200():
    ft = FakeTransport()
    ft.add_exception(TransportError("network", "n"))
    ft.add_response(200, OK_BODY)
    result = _prov(transport=ft).chat([ChatMessage(role="user", content="hi")])
    assert ft.request_count() == 2


def test_no_retry_401():
    ft = FakeTransport()
    ft.add_response(401, {"error": {"message": "bad"}})
    with pytest.raises(ProviderError):
        _prov(transport=ft).chat([ChatMessage(role="user", content="hi")])
    assert ft.request_count() == 1


def test_no_retry_429():
    ft = FakeTransport()
    ft.add_response(429, {"error": {"message": "slow down"}})
    with pytest.raises(ProviderError):
        _prov(transport=ft).chat([ChatMessage(role="user", content="hi")])
    assert ft.request_count() == 1


def test_retry_exhausted_network_raises_network_error():
    ft = FakeTransport()
    ft.add_exception(TransportError("network", "n"))
    ft.add_exception(TransportError("network", "n"))
    with pytest.raises(ProviderError) as e:
        _prov(transport=ft).chat([ChatMessage(role="user", content="hi")])
    assert e.value.code == NETWORK_ERROR
    assert ft.request_count() == 2


def test_retry_exhausted_timeout_raises_timeout():
    ft = FakeTransport()
    ft.add_exception(TransportError("timeout", "t"))
    ft.add_exception(TransportError("timeout", "t"))
    with pytest.raises(ProviderError) as e:
        _prov(transport=ft).chat([ChatMessage(role="user", content="hi")])
    assert e.value.code == TIMEOUT
    assert ft.request_count() == 2


# ── 密钥 / Keyless(§19-22) ───────────────────────────────

def test_keyless_no_authorization_header():
    ft = FakeTransport()
    ft.add_response(200, OK_BODY)
    p = _prov(_cfg(secret_reference=""), transport=ft)
    p.chat([ChatMessage(role="user", content="hi")])
    assert "Authorization" not in ft.requests[0]["headers"]


def test_auth_bearer_header():
    key = fake_key()
    ft = FakeTransport()
    ft.add_response(200, OK_BODY)
    p = _prov(_cfg(secret_reference="my-ref"), store=FakeSecretStore({"my-ref": key}), transport=ft)
    p.chat([ChatMessage(role="user", content="hi")])
    assert ft.requests[0]["headers"]["Authorization"] == f"Bearer {key}"


def test_key_not_found_maps_to_key_not_configured():
    ft = FakeTransport()
    ft.add_response(200, OK_BODY)
    p = _prov(_cfg(secret_reference="nope"), store=FakeSecretStore({}), transport=ft)
    with pytest.raises(ProviderError) as e:
        p.chat([ChatMessage(role="user", content="hi")])
    assert e.value.code == KEY_NOT_CONFIGURED
    assert "尚未配置" in e.value.message
    assert "nope" in e.value.message  # reference 名可显示
    assert ft.request_count() == 0  # 不发请求


def test_secret_never_in_provider_error():
    key = fake_key(prefix="sk-LEAKTEST")
    ft = FakeTransport()
    ft.add_response(401, {"error": {"message": "invalid key"}})
    p = _prov(_cfg(secret_reference="r"), store=FakeSecretStore({"r": key}), transport=ft)
    with pytest.raises(ProviderError) as e:
        p.chat([ChatMessage(role="user", content="hi")])
    assert key not in str(e.value)
    assert key not in e.value.message


# ── endpoint(§14-16) ─────────────────────────────────────

def test_endpoint_trailing_slash_no_double_slash():
    ft = FakeTransport()
    ft.add_response(200, OK_BODY)
    p = _prov(_cfg(base_url="https://example.com/v1/"), transport=ft)
    p.chat([ChatMessage(role="user", content="hi")])
    assert ft.requests[0]["url"] == "https://example.com/v1/chat/completions"


def test_endpoint_full_already_used_as_is():
    ft = FakeTransport()
    ft.add_response(200, OK_BODY)
    p = _prov(_cfg(base_url="https://example.com/v1/chat/completions"), transport=ft)
    p.chat([ChatMessage(role="user", content="hi")])
    assert ft.requests[0]["url"] == "https://example.com/v1/chat/completions"
    assert "chat/completions/chat/completions" not in ft.requests[0]["url"]


def test_base_url_missing_config_error():
    with pytest.raises(ProviderError) as e:
        _prov(_cfg(base_url="")).chat([ChatMessage(role="user", content="hi")])
    assert e.value.code == CONFIG_ERROR


def test_base_url_bad_scheme_rejected():
    ft = FakeTransport()
    with pytest.raises(ProviderError) as e:
        _prov(_cfg(base_url="file:///etc/passwd"), transport=ft).chat([ChatMessage(role="user", content="hi")])
    assert e.value.code == CONFIG_ERROR
    assert ft.request_count() == 0


# ── tools(§18) ───────────────────────────────────────────

TOOLS = [{"type": "function", "function": {"name": "get_weather", "parameters": {"type": "object"}}}]


def test_tools_sent_when_provided():
    ft = FakeTransport()
    ft.add_response(200, OK_BODY)
    p = _prov(transport=ft)
    p.chat([ChatMessage(role="user", content="hi")], tools=TOOLS)
    assert ft.requests[0]["payload"]["tools"] == TOOLS


def test_tool_calls_capability_false_rejects_tools():
    ft = FakeTransport()
    ft.add_response(200, OK_BODY)
    p = _prov(_cfg(tool_calls=False), transport=ft)
    with pytest.raises(ProviderError) as e:
        p.chat([ChatMessage(role="user", content="hi")], tools=TOOLS)
    assert e.value.code == CONFIG_ERROR
    assert ft.request_count() == 0


def test_empty_messages_rejected():
    ft = FakeTransport()
    with pytest.raises(ProviderError) as e:
        _prov(transport=ft).chat([])
    assert e.value.code == CONFIG_ERROR


# ── unicode 请求体(§142) ─────────────────────────────────

def test_unicode_messages_serialized():
    ft = FakeTransport()
    ft.add_response(200, OK_BODY)
    p = _prov(transport=ft)
    p.chat([ChatMessage(role="user", content="山河不记🌙 中文标点。")])
    payload = ft.requests[0]["payload"]
    assert payload["messages"][0]["content"] == "山河不记🌙 中文标点。"
