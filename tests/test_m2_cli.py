"""M2 CLI 集成测试(subprocess 真实运行 CLI + local mock server)。

覆盖: config validate 离线 / show / test-provider / chat(流式+非流式+keyless)/
usage summary+recent / key-status / Secret 全路径不泄漏。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "tests"))

from mock_server import MockServer  # noqa: E402
from conftest import fake_key  # noqa: E402

CLI = PROJECT_ROOT / "adapters" / "cli" / "main.py"


def _run(tmp_path, *args, env_extra=None, stdin_text=None, **kw):
    settings = tmp_path / "settings.json"
    data_dir = tmp_path / "novels"
    data_dir.mkdir(exist_ok=True)
    usage_path = tmp_path / "usage.jsonl"
    cmd = [sys.executable, str(CLI), "--config", str(settings),
           "--data-dir", str(data_dir), "--usage-path", str(usage_path), *args]
    env = dict(os.environ)
    env["NOVEL_DISABLE_KEYRING"] = "1"
    if env_extra:
        env.update(env_extra)
    return subprocess.run(cmd, capture_output=True, text=True, cwd=PROJECT_ROOT,
                          input=stdin_text, env=env, **kw)


def _write_settings(tmp_path, *, base_url, model="m1", secret_reference="", role_cfg=None):
    settings = tmp_path / "settings.json"
    data = {
        "default_model": {
            "provider": "openai_compatible", "base_url": base_url, "model": model,
            "temperature": 0.8,
            "capabilities": {"tool_calls": True, "vision": False, "max_context_tokens": 128000},
            "secret_reference": secret_reference,
        },
        "models": role_cfg or {},
    }
    settings.write_text(json.dumps(data), encoding="utf-8")
    return settings


# ── config validate 离线(§49, 107) ────────────────────────

def test_config_validate_never_touches_network(monkeypatch, tmp_path):
    """即使 base_url 指向不可连接地址, validate 也立即本地完成。"""
    import httpx

    def boom(*a, **k):
        raise AssertionError("config validate 触发了网络调用!")

    monkeypatch.setattr(httpx, "Client", boom)
    from core.config import Settings, validate_settings
    s = Settings.load(tmp_path / "settings.json")
    s.default_model.base_url = "http://127.0.0.1:9"
    s.default_model.model = "m"
    s.default_model.secret_reference = ""
    issues = validate_settings(s, None)
    assert not any(i.severity == "error" for i in issues)  # keyless warning 不算错误


def test_config_validate_offline_cli(tmp_path):
    r = _run(tmp_path, "config", "set", "default_model.base_url", "http://127.0.0.1:9")
    assert r.returncode == 0, r.stderr
    r = _run(tmp_path, "config", "set", "default_model.model", "m1")
    assert r.returncode == 0
    r = _run(tmp_path, "config", "validate")
    assert r.returncode == 0, r.stdout + r.stderr  # 立即完成, 不联网
    assert "未联网" in r.stdout


def test_config_validate_rejects_key_in_url(tmp_path):
    # config set 在写入前直接拒绝(比 validate 更早的防线)
    r = _run(tmp_path, "config", "set", "default_model.base_url", "http://x.com/v1?api_key=secret123")
    assert r.returncode == 1
    assert "拒绝" in r.stdout
    assert "secret123" not in r.stdout  # 不回显 secret
    # 手工写坏 URL 到 settings → validate 也拒绝
    _write_settings(tmp_path, base_url="http://x.com/v1?api_key=secret123")
    r2 = _run(tmp_path, "config", "validate")
    assert r2.returncode == 1
    assert "base_url" in r2.stdout


def test_config_validate_unsupported_provider(tmp_path):
    _run(tmp_path, "config", "set", "default_model.provider", "anthropic")
    _run(tmp_path, "config", "set", "default_model.base_url", "http://127.0.0.1:9")
    _run(tmp_path, "config", "set", "default_model.model", "m")
    r = _run(tmp_path, "config", "validate")
    assert r.returncode == 1
    assert "anthropic" in r.stdout


# ── config show / key-status(§129, 133) ───────────────────

def test_config_show_key_status(tmp_path):
    _write_settings(tmp_path, base_url="http://127.0.0.1:9", secret_reference="my-ref")
    r = _run(tmp_path, "config", "show", env_extra={"NOVEL_API_KEY_MY_REF": fake_key()})
    assert r.returncode == 0
    assert "key: configured" in r.stdout
    assert fake_key() not in r.stdout


def test_config_key_status_missing(tmp_path):
    # keyring 禁用(env 模式): 无 env key → keyring 不可用 → 明确报 unavailable
    r = _run(tmp_path, "config", "key-status", "nope-ref")
    assert r.returncode == 1
    assert "unavailable" in r.stdout
    assert fake_key() not in r.stdout


def test_config_key_status_configured(tmp_path):
    r = _run(tmp_path, "config", "key-status", "my-ref",
             env_extra={"NOVEL_API_KEY_MY_REF": fake_key()})
    assert r.returncode == 0
    assert "configured" in r.stdout
    assert fake_key() not in r.stdout


# ── config test-provider(§52-56, 104) ─────────────────────

def test_config_test_provider_success(tmp_path, server: MockServer):
    key = fake_key(prefix="sk-M2CLI")
    server.require_auth = key
    _write_settings(tmp_path, base_url=server.base_url, secret_reference="my-ref")
    r = _run(tmp_path, "config", "test-provider",
             env_extra={"NOVEL_API_KEY_MY_REF": key})
    assert r.returncode == 0, r.stdout + r.stderr
    assert "Provider reachable" in r.stdout
    assert "mock-model" in r.stdout
    assert "latency" in r.stdout
    assert "usage:" in r.stdout
    assert key not in r.stdout and key not in r.stderr
    assert server.last_auth() == f"Bearer {key}"


def test_config_test_provider_auth_error(tmp_path, server: MockServer):
    key = fake_key(prefix="sk-M2CLI")
    server.require_auth = key + "-wrong"
    _write_settings(tmp_path, base_url=server.base_url, secret_reference="my-ref")
    r = _run(tmp_path, "config", "test-provider",
             env_extra={"NOVEL_API_KEY_MY_REF": key})
    assert r.returncode == 1
    assert "失败" in r.stdout
    assert "Traceback" not in r.stderr
    assert key not in r.stdout and key not in r.stderr


def test_config_test_provider_missing_key(tmp_path, server: MockServer):
    _write_settings(tmp_path, base_url=server.base_url, secret_reference="nope-ref")
    r = _run(tmp_path, "config", "test-provider")
    assert r.returncode == 1
    # env 模式 keyring 禁用: 可能显示"尚未配置"(keyring 可用时)或"凭据管理器不可用"
    assert ("尚未配置" in r.stdout) or ("不可用" in r.stdout)
    assert "Traceback" not in r.stderr
    assert server.request_count() == 0  # 未发请求


def test_config_test_provider_role_fallback(tmp_path, server: MockServer):
    _write_settings(tmp_path, base_url=server.base_url, secret_reference="")
    r = _run(tmp_path, "config", "test-provider", "--role", "writer")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "未配置, 使用 default_model" in r.stdout


# ── chat(§57-64, 105-106) ─────────────────────────────────

def test_chat_no_stream(tmp_path, server: MockServer):
    _write_settings(tmp_path, base_url=server.base_url, secret_reference="")
    r = _run(tmp_path, "chat", "你好", "--no-stream")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "你好" in r.stdout
    assert "usage:" in r.stdout


def test_chat_stream_default(tmp_path, server: MockServer):
    server.stream_mode = "full"
    server.stream_chunks = [
        '{"choices":[{"delta":{"content":"AI"}}]}',
        '{"choices":[{"delta":{"content":" Novel"}}]}',
        '{"choices":[{"delta":{"content":" Studio"}}]}',
        '{"choices":[{"delta":{},"finish_reason":"stop"}]}',
        "[DONE]",
    ]
    _write_settings(tmp_path, base_url=server.base_url, secret_reference="")
    r = _run(tmp_path, "chat", "你好")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "AI Novel Studio" in r.stdout  # 最终拼接正确(§106)


def test_chat_with_system_and_role(tmp_path, server: MockServer):
    _write_settings(tmp_path, base_url=server.base_url, secret_reference="")
    r = _run(tmp_path, "chat", "hi", "--system", "你是一个简洁助手", "--role", "writer",
             "--no-stream")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "未配置, 使用 default_model" in r.stdout
    body = server.last_body_json()
    assert body["messages"][0] == {"role": "system", "content": "你是一个简洁助手"}
    assert body["messages"][1] == {"role": "user", "content": "hi"}


def test_chat_empty_prompt_rejected(tmp_path):
    _write_settings(tmp_path, base_url="http://127.0.0.1:9")
    r = _run(tmp_path, "chat", "")
    assert r.returncode == 1
    assert "不能为空" in r.stdout


def test_chat_keyless_no_authorization(tmp_path, server: MockServer):
    server.forbid_auth = True
    _write_settings(tmp_path, base_url=server.base_url, secret_reference="")
    r = _run(tmp_path, "chat", "hi", "--no-stream")
    assert r.returncode == 0, r.stdout + r.stderr
    assert server.auth_violation is False


def test_chat_invalid_temperature(tmp_path):
    _write_settings(tmp_path, base_url="http://127.0.0.1:9")
    r = _run(tmp_path, "chat", "hi", "--temperature", "nan")
    assert r.returncode == 1
    assert "有限数字" in r.stdout
    r2 = _run(tmp_path, "chat", "hi", "--temperature", "-1")
    assert r2.returncode == 1


def test_chat_stream_interrupted_no_retry(tmp_path, server: MockServer):
    server.stream_mode = "interrupt"
    server.stream_chunks = ['{"choices":[{"delta":{"content":"Hello"}}]}']
    _write_settings(tmp_path, base_url=server.base_url, secret_reference="")
    r = _run(tmp_path, "chat", "你好")
    assert r.returncode == 1
    assert "中断" in r.stdout + r.stderr
    assert server.request_count() == 1  # 不重试
    assert "Traceback" not in r.stderr


def test_chat_usage_recorded(tmp_path, server: MockServer):
    _write_settings(tmp_path, base_url=server.base_url, secret_reference="")
    r = _run(tmp_path, "chat", "hi", "--no-stream")
    assert r.returncode == 0
    r2 = _run(tmp_path, "usage", "summary")
    assert r2.returncode == 0
    assert "requests: 1" in r2.stdout
    assert "total tokens: 12" in r2.stdout


def test_usage_recent(tmp_path, server: MockServer):
    _write_settings(tmp_path, base_url=server.base_url, secret_reference="")
    _run(tmp_path, "chat", "hi", "--no-stream")
    r = _run(tmp_path, "usage", "recent", "--limit", "20")
    assert r.returncode == 0
    assert "mock-model" in r.stdout


# ── Secret 全路径不泄漏(§90, 125) ─────────────────────────

def test_secret_never_leaked_anywhere(tmp_path, server: MockServer):
    key = fake_key(prefix="sk-GLOBALLEAK")
    server.responses.append((401, {"error": {"message": f"invalid {key}"}}))
    _write_settings(tmp_path, base_url=server.base_url, secret_reference="my-ref")

    # test-provider 失败路径
    r = _run(tmp_path, "config", "test-provider", env_extra={"NOVEL_API_KEY_MY_REF": key})
    assert r.returncode == 1
    assert key not in r.stdout and key not in r.stderr

    # chat 失败路径(错误体含 fake key)
    r2 = _run(tmp_path, "chat", "hi", "--no-stream", env_extra={"NOVEL_API_KEY_MY_REF": key})
    assert key not in r2.stdout and key not in r2.stderr

    # usage 文件 / settings / 小说数据
    for f in (tmp_path / "usage.jsonl", tmp_path / "settings.json"):
        text = f.read_text(encoding="utf-8") if f.exists() else ""
        assert key not in text, f"Key 泄漏到 {f}"

    # 异常对象层面由 test_provider_http 覆盖; CLI 无 traceback
    assert "Traceback" not in r.stderr and "Traceback" not in r2.stderr
