"""内部统一数据模型测试: 序列化 / 估算。"""
from __future__ import annotations

from core.config import ModelConfig
from llm.provider import BaseProvider
from llm.types import ChatMessage, ToolCall, Usage


def _cfg(**kw) -> ModelConfig:
    base = dict(base_url="https://example.com/v1", model="m1")
    base.update(kw)
    return ModelConfig(**base)


# ── ChatMessage 序列化 ────────────────────────────────────

def test_message_to_dict_basic():
    m = ChatMessage(role="user", content="你好")
    assert m.to_dict() == {"role": "user", "content": "你好"}


def test_message_to_dict_with_tool_call_id():
    m = ChatMessage(role="tool", content="result", tool_call_id="call_1")
    d = m.to_dict()
    assert d["tool_call_id"] == "call_1"


def test_message_to_dict_with_tool_calls():
    m = ChatMessage(role="assistant", content="",
                    tool_calls=[ToolCall(id="c1", name="get_weather", arguments_json='{"city":"北京"}')])
    d = m.to_dict()
    assert d["tool_calls"][0]["id"] == "c1"
    assert d["tool_calls"][0]["function"]["name"] == "get_weather"
    assert d["tool_calls"][0]["function"]["arguments"] == '{"city":"北京"}'


def test_tool_call_to_dict():
    tc = ToolCall(id="x", name="f", arguments_json="{}")
    d = tc.to_dict()
    assert d["type"] == "function"


# ── Usage ─────────────────────────────────────────────────

def test_usage_estimated_flag():
    u = Usage.estimated_usage(10, 20)
    assert u.total_tokens == 30
    assert u.estimated is True
    assert u.source == "estimated"


def test_usage_is_empty():
    assert Usage().is_empty()
    assert not Usage(prompt_tokens=1).is_empty()


# ── estimate_tokens ───────────────────────────────────────

def test_estimate_tokens_ascii():
    # "hello" 5 个 ASCII → 5//4 + 1 = 2
    assert BaseProvider.estimate_tokens("hello") == 2


def test_estimate_tokens_chinese():
    # 中文按 1 字符 1 token(近似)
    assert BaseProvider.estimate_tokens("山河不记") == 4


def test_estimate_tokens_mixed():
    t = BaseProvider.estimate_tokens("hello 你好 world")
    assert t > 0


def test_estimate_tokens_empty():
    assert BaseProvider.estimate_tokens("") == 1


def test_estimate_messages_tokens():
    msgs = [ChatMessage(role="user", content="你好")]
    assert BaseProvider.estimate_messages_tokens(msgs) > 0


# ── 超长保护 ──────────────────────────────────────────────

def test_check_input_within_context_ok():
    prov = BaseProvider(_cfg(max_context_tokens=1000))
    prov.check_input_within_context([ChatMessage(role="user", content="hi")])  # 不抛


def test_check_input_within_context_too_long():
    from llm.provider import CONFIG_ERROR, ProviderError
    prov = BaseProvider(_cfg(max_context_tokens=10))
    try:
        prov.check_input_within_context([ChatMessage(role="user", content="x" * 100)])
        raise AssertionError("应当拒绝超长输入")
    except ProviderError as e:
        assert e.code == CONFIG_ERROR
        assert "输入过长" in e.message
