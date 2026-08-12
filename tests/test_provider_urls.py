"""M2 CLOSEOUT: Base URL 安全在生产路径强制执行(持久化 + 运行时)。

- validate_provider_base_url 统一校验(urllib.parse, 非 regex/startswith)
- config set base_url 写前拒绝(settings.json 不被污染)
- Provider factory / endpoint 运行时拒绝(手工改 settings 也拦得住)
- fake secret 绝不进入 settings/stdout/stderr/ProviderError
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

import pytest  # noqa: E402

from core.config import ConfigError, ModelConfig, Settings, validate_provider_base_url, validate_settings  # noqa: E402
from llm.factory import create_provider  # noqa: E402
from llm.openai_compatible import OpenAICompatibleProvider  # noqa: E402
from llm.provider import CONFIG_ERROR, ProviderError  # noqa: E402
from llm.testing import FakeTransport  # noqa: E402
from llm.types import ChatMessage  # noqa: E402
from conftest import fake_key  # noqa: E402

CLI = PROJECT_ROOT / "adapters" / "cli" / "main.py"

ALLOWED = [
    "https://api.example.com/v1",
    "http://localhost:11434/v1",
    "http://127.0.0.1:8000/v1",
    "https://example.com/v1/chat/completions",
    "http://localhost:11434/v1/",
]

REJECTED = [
    ("", "空"),
    ("https://", "仅 scheme 无 host"),
    ("http://", "仅 scheme 无 host"),
    ("file:///etc/passwd", "file 协议"),
    ("ftp://example.com/v1", "ftp 协议"),
    ("https://user:pass@example.com/v1", "userinfo"),
    ("https://user@example.com/v1", "userinfo(仅用户名)"),
    ("https://example.com/v1?api_key=secret", "query api_key"),
    ("https://example.com/v1?API_KEY=secret", "query 大小写"),
    ("https://example.com/v1?apikey=secret", "query apikey"),
    ("https://example.com/v1?key=secret", "query key"),
    ("https://example.com/v1?token=secret", "query token"),
    ("https://example.com/v1?access_token=secret", "query access_token"),
    ("https://example.com/v1?auth=secret", "query auth"),
    ("https://example.com/v1?authorization=secret", "query authorization"),
    ("https://example.com/v1?foo=bar", "普通 query 也拒绝(防拼接歧义)"),
    ("https://example.com/v1#frag", "fragment"),
]


# ── validate_provider_base_url 全表 ───────────────────────

@pytest.mark.parametrize("url", ALLOWED)
def test_allowed_urls(url):
    assert validate_provider_base_url(url) is None, f"应允许: {url}"


@pytest.mark.parametrize("url,_label", REJECTED)
def test_rejected_urls(url, _label):
    err = validate_provider_base_url(url)
    assert err is not None, f"应拒绝: {url}"
    if url:
        assert url not in err, "错误消息不得包含完整危险 URL(防回显 secret)"


# ── config set 写前拒绝(§1, 2) ───────────────────────────

@pytest.mark.parametrize("url,_label", REJECTED)
def test_set_value_rejects_before_write(tmp_path, url, _label):
    s = Settings.load(tmp_path / "settings.json")
    with pytest.raises(ConfigError):
        s.set_value("default_model.base_url", url)
    assert not (tmp_path / "settings.json").exists() or "base_url" not in (
        tmp_path / "settings.json").read_text(encoding="utf-8")


def test_set_value_allows_valid_url(tmp_path):
    s = Settings.load(tmp_path / "settings.json")
    s.set_value("default_model.base_url", "https://api.example.com/v1")
    data = json.loads((tmp_path / "settings.json").read_text(encoding="utf-8"))
    assert data["default_model"]["base_url"] == "https://api.example.com/v1"


def test_set_value_rejects_role_model_url(tmp_path):
    s = Settings.load(tmp_path / "settings.json")
    with pytest.raises(ConfigError):
        s.set_value("models.writer.base_url", "https://user:pass@x.com/v1")


# ── config set CLI: 拒绝 + 不泄漏(§2) ─────────────────────

def _run_cli(tmp_path, *args):
    cmd = [sys.executable, str(CLI), "--config", str(tmp_path / "settings.json"), *args]
    return subprocess.run(cmd, capture_output=True, text=True, cwd=PROJECT_ROOT)


@pytest.mark.parametrize("bad_url", [
    "https://example.com/v1?api_key=sk-SECRETQUERY",
    "https://user:sk-SECRETUSER@example.com/v1",
])
def test_cli_set_rejects_secret_url_no_leak(tmp_path, bad_url):
    key = fake_key(prefix="sk-URLSET")
    url = bad_url.replace("sk-SECRETQUERY", key).replace("sk-SECRETUSER", key)
    r = _run_cli(tmp_path, "config", "set", "default_model.base_url", url)
    assert r.returncode == 1
    # 安全错误, 不打印完整危险 URL / secret
    assert key not in r.stdout and key not in r.stderr
    assert "拒绝" in r.stdout
    # settings.json 未被污染
    p = tmp_path / "settings.json"
    if p.exists():
        assert key not in p.read_text(encoding="utf-8")
        assert "base_url" not in p.read_text(encoding="utf-8")


def test_cli_set_valid_url_ok(tmp_path):
    r = _run_cli(tmp_path, "config", "set", "default_model.base_url", "https://api.example.com/v1")
    assert r.returncode == 0
    assert "已设置" in r.stdout


# ── validate_settings 集成 ────────────────────────────────

def test_validate_settings_rejects_bad_url():
    s = Settings.load(Path("x"))  # 不存在 → 默认
    s.default_model.base_url = "https://user:pass@x.com/v1"
    s.default_model.model = "m"
    s.default_model.secret_reference = ""
    issues = validate_settings(s, None)
    assert any(i.severity == "error" and "base_url" in i.message for i in issues)


# ── Provider 生产路径运行时拒绝(§3) ───────────────────────

def _cfg(base_url: str) -> ModelConfig:
    return ModelConfig(base_url=base_url, model="m1")


@pytest.mark.parametrize("url,_label", REJECTED)
def test_factory_rejects_bad_url(url, _label):
    with pytest.raises(ProviderError) as e:
        create_provider(_cfg(url))
    assert e.value.code == CONFIG_ERROR


def test_endpoint_revalidates_on_call():
    """手工构造危险 URL 绕过 CLI → provider._endpoint 仍拒绝。"""
    ft = FakeTransport()
    p = OpenAICompatibleProvider(_cfg("https://example.com/v1?api_key=leak"), transport=ft)
    with pytest.raises(ProviderError) as e:
        list(p.stream_chat([ChatMessage(role="user", content="hi")]))
    assert e.value.code == CONFIG_ERROR
    assert ft.request_count() == 0  # 未发请求


def test_endpoint_secret_not_in_provider_error():
    key = fake_key(prefix="sk-ENDPOINT")
    p = OpenAICompatibleProvider(_cfg(f"https://user:{key}@example.com/v1"))
    with pytest.raises(ProviderError) as e:
        p.chat([ChatMessage(role="user", content="hi")])
    assert key not in str(e.value)
    assert key not in e.value.message


def test_endpoint_full_and_query_fragment_behavior():
    # 完整 endpoint 支持(不重复追加)
    ft = FakeTransport()
    ft.add_response(200, {"choices": [{"message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}]})
    p = OpenAICompatibleProvider(_cfg("https://example.com/v1/chat/completions"), transport=ft)
    p.chat([ChatMessage(role="user", content="hi")])
    assert ft.requests[0]["url"] == "https://example.com/v1/chat/completions"

    # 带 query 的 base → CONFIG_ERROR(第一版安全优先)
    p2 = OpenAICompatibleProvider(_cfg("https://example.com/v1?foo=bar"), transport=FakeTransport())
    with pytest.raises(ProviderError) as e:
        p2.chat([ChatMessage(role="user", content="hi")])
    assert e.value.code == CONFIG_ERROR
