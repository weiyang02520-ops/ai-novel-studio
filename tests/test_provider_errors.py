"""Provider 错误安全测试: HTML/截断/Key 不泄漏/状态映射细节。"""
from __future__ import annotations

import pytest

from llm.openai_compatible import _safe_error_message, map_http_error
from llm.provider import (
    AUTH_ERROR,
    BAD_REQUEST,
    ENDPOINT_ERROR,
    HTTP_ERROR,
    NOT_FOUND,
    PERMISSION_ERROR,
    RATE_LIMIT,
    SERVER_ERROR,
    TIMEOUT,
)
from conftest import fake_key


# ── _safe_error_message(§73-74) ───────────────────────────

def test_html_error_never_json_parsed():
    msg = _safe_error_message("<html><body>Cloudflare 403</body></html>", "text/html; charset=utf-8")
    assert "HTML" in msg
    assert "<html>" not in msg


def test_html_detected_by_content_prefix():
    msg = _safe_error_message("<html>forbidden</html>", "")
    assert "HTML" in msg


def test_long_body_truncated():
    body = "x" * 5000
    msg = _safe_error_message(body, "")
    assert len(msg) <= 700  # 600 + "..."
    assert msg.endswith("...")


def test_json_error_message_extracted():
    msg = _safe_error_message('{"error": {"message": "invalid api key", "code": "invalid_api_key"}}', "application/json")
    assert msg == "invalid api key"


def test_json_error_string_form():
    msg = _safe_error_message('{"error": "some error"}', "application/json")
    assert msg == "some error"


def test_secret_in_error_body_never_leaked():
    key = fake_key(prefix="sk-ERRLEAK")
    body = f'{{"error": {{"message": "auth failed with key {key}"}}}}'
    e = map_http_error(401, body, {"content-type": "application/json"}, api_key=key)
    assert key not in e.message
    # 400 类错误消息会携带 body detail — 该路径也必须过滤
    e2 = map_http_error(400, body, {"content-type": "application/json"}, api_key=key)
    assert key not in e2.message


def test_plain_text_body_truncated():
    body = "plain text body " * 100
    msg = _safe_error_message(body, "text/plain")
    assert len(msg) <= 700


# ── map_http_error 详情 ──────────────────────────────────

def test_401_message_safe_and_clear():
    e = map_http_error(401, '{"error":{"message":"Incorrect API key provided: sk-xxx"}}')
    assert e.code == AUTH_ERROR
    assert "sk-xxx" not in e.message  # 不泄漏 body 中的 key
    assert "无效或未授权" in e.message


def test_403_permission():
    e = map_http_error(403, "forbidden")
    assert e.code == PERMISSION_ERROR


def test_404_not_found_hint():
    e = map_http_error(404, "not found")
    assert e.code == NOT_FOUND
    assert "Base URL" in e.message


def test_405_endpoint_hint():
    e = map_http_error(405, "method not allowed")
    assert e.code == ENDPOINT_ERROR
    assert "OpenAI-compatible" in e.message


def test_408_timeout_retryable():
    e = map_http_error(408, "")
    assert e.code == TIMEOUT
    assert e.retryable


def test_429_rate_limit():
    e = map_http_error(429, "rate limited", {"retry-after": "30"})
    assert e.code == RATE_LIMIT
    assert "30" in e.message


def test_500_server_error_retryable():
    e = map_http_error(500, "boom")
    assert e.code == SERVER_ERROR
    assert e.retryable


def test_unknown_status_418():
    e = map_http_error(418, "teapot")
    assert e.code == HTTP_ERROR
    assert e.status_code == 418


def test_400_unknown_bad_request():
    e = map_http_error(400, '{"error":{"message":"something else"}}')
    assert e.code == BAD_REQUEST
    assert "something else" in e.message
