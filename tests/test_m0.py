"""M0 单元测试: 配置系统 + SecretStore。

运行: python -m pytest tests/ -v
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

import pytest  # noqa: E402

from core.config import (  # noqa: E402
    DEFAULT_SETTINGS,
    ConfigError,
    Settings,
    validate_settings,
)
from llm.secret_store import (  # noqa: E402
    CompositeSecretStore,
    EnvSecretStore,
    KeyringSecretStore,
    SecretStoreError,
)


class FakeKeyring:
    """测试用假 keyring 后端。"""

    def __init__(self):
        self._d: dict[str, str] = {}

    def get_password(self, service, ref):
        return self._d.get(ref)

    def set_password(self, service, ref, value):
        self._d[ref] = value

    def delete_password(self, service, ref):
        self._d.pop(ref, None)


class FakeKR:
    """与 KeyringSecretStore 错误语义一致的假后端。"""

    def __init__(self, kr):
        self._kr = kr
        self.available = True

    def get(self, ref):
        v = self._kr.get_password("ai-novel-studio", ref)
        if v:
            return v
        raise SecretStoreError("KEY_NOT_FOUND", f"未找到 Key: {ref}")

    def set(self, ref, value):
        self._kr.set_password("ai-novel-studio", ref, value)

    def delete(self, ref):
        self._kr.delete_password("ai-novel-studio", ref)


def fake_store() -> CompositeSecretStore:
    s = CompositeSecretStore()
    s._keyring = FakeKR(FakeKeyring())
    return s


# ── Settings 基本 ─────────────────────────────────────────

def test_settings_default_and_save(tmp_path):
    p = tmp_path / "settings.json"
    s = Settings.load(p)  # 不存在 → 默认
    assert s.default_model.provider == "openai_compatible"
    s.default_model.base_url = "https://api.deepseek.com/v1"
    s.default_model.model = "deepseek-chat"
    s.save()
    s2 = Settings.load(p)
    assert s2.default_model.base_url == "https://api.deepseek.com/v1"
    assert s2.workflow["max_review_rounds"] == 3  # 默认值保留


def test_settings_set_model_role(tmp_path):
    p = tmp_path / "settings.json"
    s = Settings.load(p)
    s.set_value("models.writer.base_url", "http://127.0.0.1:11434/v1")
    s.set_value("models.writer.model", "qwen3")
    s.save()
    s2 = Settings.load(p)
    assert s2.model_for("writer").base_url == "http://127.0.0.1:11434/v1"
    assert s2.model_for("unknown").model == ""  # 回退 default


# ── 问题 2: ConfigError / 损坏配置 ─────────────────────────

def test_load_invalid_json_raises_configerror(tmp_path):
    p = tmp_path / "settings.json"
    p.write_text("{ not valid json", encoding="utf-8")
    with pytest.raises(ConfigError) as e:
        Settings.load(p)
    assert "JSON" in str(e.value)


def test_load_root_not_object(tmp_path):
    p = tmp_path / "settings.json"
    p.write_text("[1, 2, 3]", encoding="utf-8")
    with pytest.raises(ConfigError):
        Settings.load(p)


def test_load_models_not_object(tmp_path):
    p = tmp_path / "settings.json"
    p.write_text(json.dumps({"models": "not-object"}), encoding="utf-8")
    with pytest.raises(ConfigError):
        Settings.load(p)


def test_load_temperature_bad_type(tmp_path):
    p = tmp_path / "settings.json"
    p.write_text(json.dumps({"default_model": {"temperature": "hot"}}), encoding="utf-8")
    with pytest.raises(ConfigError) as e:
        Settings.load(p)
    assert "temperature" in str(e.value)


def test_load_max_context_bad_type(tmp_path):
    p = tmp_path / "settings.json"
    p.write_text(json.dumps({"default_model": {"capabilities": {"max_context_tokens": "many"}}}), encoding="utf-8")
    with pytest.raises(ConfigError) as e:
        Settings.load(p)
    assert "max_context_tokens" in str(e.value)


def test_cli_validate_invalid_json_no_traceback(tmp_path):
    import subprocess
    cfg = tmp_path / "settings.json"
    cfg.write_text("{ broken", encoding="utf-8")
    cli = PROJECT_ROOT / "adapters" / "cli" / "main.py"
    r = subprocess.run([sys.executable, str(cli), "--config", str(cfg), "config", "validate"],
                       capture_output=True, text=True, cwd=PROJECT_ROOT)
    assert r.returncode == 1
    assert "Traceback" not in r.stderr
    assert "JSON" in r.stdout


# ── 问题 3: 默认配置合并 ───────────────────────────────────

def test_defaults_survive_save_reload(tmp_path):
    """空配置 → 改 base_url → save → reload → 所有默认值仍在。"""
    p = tmp_path / "settings.json"
    s = Settings.load(p)
    s.set_value("default_model.base_url", "https://api.example.com/v1")
    # 手动 set_value 已 save; 再 reload 检查
    s2 = Settings.load(p)
    assert s2.default_model.base_url == "https://api.example.com/v1"
    assert s2.context["reserve_output_tokens"] == DEFAULT_SETTINGS["context"]["reserve_output_tokens"]
    assert s2.context["max_recent_chapters"] == DEFAULT_SETTINGS["context"]["max_recent_chapters"]
    assert s2.context["max_recent_text_chars"] == DEFAULT_SETTINGS["context"]["max_recent_text_chars"]
    assert s2.workflow["max_review_rounds"] == DEFAULT_SETTINGS["workflow"]["max_review_rounds"]
    assert s2.workflow["max_tool_calls_per_turn"] == DEFAULT_SETTINGS["workflow"]["max_tool_calls_per_turn"]
    assert s2.history["max_snapshots"] == DEFAULT_SETTINGS["history"]["max_snapshots"]
    assert s2.auto_accept is False


# ── 问题 4: set 白名单 + 类型转换 ─────────────────────────

def test_set_unknown_field_rejected(tmp_path):
    s = Settings.load(tmp_path / "settings.json")
    with pytest.raises(ConfigError) as e:
        s.set_value("default_model.nonexistent", "x")
    assert "未知或不允许" in str(e.value)


def test_set_sensitive_field_rejected(tmp_path):
    s = Settings.load(tmp_path / "settings.json")
    for bad in ["default_model.api_key", "workflow.token", "history.password", "secret", "credential"]:
        with pytest.raises(ConfigError) as e:
            s.set_value(bad, "x")
        assert "敏感" in str(e.value) or "未知" in str(e.value)


def test_set_type_conversion(tmp_path):
    p = tmp_path / "settings.json"
    s = Settings.load(p)
    s.set_value("default_model.temperature", "0.5")  # str → float
    assert s.default_model.temperature == 0.5
    s.set_value("workflow.max_review_rounds", "5")   # str → int
    assert s.workflow["max_review_rounds"] == 5
    s.set_value("auto_accept", "true")               # str → bool
    assert s.auto_accept is True
    with pytest.raises(ConfigError):
        s.set_value("workflow.max_review_rounds", "abc")  # 非法 int


# ── validate(不联网) ───────────────────────────────────────

def test_validate_default_no_url_is_error(tmp_path):
    s = Settings.load(tmp_path / "settings.json")
    issues = validate_settings(s, secret_store=None)
    assert any(i.severity == "error" for i in issues)


def test_validate_ok_with_url_and_no_key(tmp_path):
    p = tmp_path / "settings.json"
    s = Settings.load(p)
    s.default_model.base_url = "https://api.example.com/v1"
    s.default_model.model = "gpt-x"
    s.save()
    issues = validate_settings(s, secret_store=None)
    assert not any(i.severity == "error" for i in issues)


def test_validate_bad_url(tmp_path):
    s = Settings.load(tmp_path / "settings.json")
    s.default_model.base_url = "ftp://bad"
    s.default_model.model = "m"
    issues = validate_settings(s, secret_store=None)
    assert any("base_url" in i.message for i in issues if i.severity == "error")


def test_validate_missing_secret(tmp_path):
    s = Settings.load(tmp_path / "settings.json")
    s.default_model.base_url = "https://api.example.com/v1"
    s.default_model.model = "m"
    s.default_model.secret_reference = "ghost-ref"
    issues = validate_settings(s, secret_store=fake_store())
    assert any("ghost-ref" in i.message for i in issues if i.severity == "error")


# ── 问题 5: SecretStore 错误语义 ───────────────────────────

def test_secret_store_set_get_delete():
    store = fake_store()
    store.set("test-ref", "sk-fake-value")
    assert store.get("test-ref") == "sk-fake-value"
    assert store.exists("test-ref")
    store.delete("test-ref")
    with pytest.raises(SecretStoreError) as e:
        store.get("test-ref")
    assert e.value.code == "KEY_NOT_FOUND"
    assert not store.exists("test-ref")


def test_secret_store_env_priority(monkeypatch):
    store = fake_store()
    store.set("test-ref", "sk-keyring-value")
    monkeypatch.setenv("NOVEL_API_KEY_TEST_REF", "sk-env-value")
    assert store.get("test-ref") == "sk-env-value"
    monkeypatch.delenv("NOVEL_API_KEY_TEST_REF")
    assert store.get("test-ref") == "sk-keyring-value"


def test_env_store_no_write():
    store = EnvSecretStore()
    with pytest.raises(SecretStoreError) as e:
        store.set("x", "y")
    assert e.value.code == "BACKEND_UNAVAILABLE"


def test_env_store_missing_key():
    store = EnvSecretStore()
    with pytest.raises(SecretStoreError) as e:
        store.get("no-such-ref")
    assert e.value.code == "KEY_NOT_FOUND"


def test_keyring_unavailable_raises():
    """keyring 不可用 → BACKEND_UNAVAILABLE, 不吞成 None。"""
    store = KeyringSecretStore()
    store._keyring = None  # 模拟未安装
    with pytest.raises(SecretStoreError) as e:
        store.get("any")
    assert e.value.code == "BACKEND_UNAVAILABLE"


def test_composite_write_without_keyring():
    store = CompositeSecretStore()
    store._keyring = KeyringSecretStore()
    store._keyring._keyring = None
    with pytest.raises(SecretStoreError) as e:
        store.set("x", "y")
    assert e.value.code == "BACKEND_UNAVAILABLE"


# ── CLI 冒烟 ───────────────────────────────────────────────

import subprocess  # noqa: E402


def _run_cli(*args, env=None):
    cli = PROJECT_ROOT / "adapters" / "cli" / "main.py"
    return subprocess.run(
        [sys.executable, str(cli), *args],
        capture_output=True, text=True, cwd=PROJECT_ROOT, env=env,
    )


def test_cli_help():
    r = _run_cli("--help")
    assert r.returncode == 0
    assert "ai-novel-studio" in r.stdout
    assert "config" in r.stdout


def test_cli_config_validate_empty(tmp_path):
    r = _run_cli("--config", str(tmp_path / "settings.json"), "config", "validate")
    assert r.returncode == 1
    assert "base_url" in r.stdout
    assert "Traceback" not in r.stderr


def test_cli_config_validate_ok(tmp_path):
    cfg = tmp_path / "settings.json"
    cfg.write_text(json.dumps({
        "default_model": {
            "provider": "openai_compatible",
            "base_url": "https://api.example.com/v1",
            "model": "gpt-x",
            "secret_reference": "",
        }
    }), encoding="utf-8")
    r = _run_cli("--config", str(cfg), "config", "validate")
    assert r.returncode == 0
    assert "配置有效" in r.stdout


def test_cli_config_set(tmp_path):
    cfg = tmp_path / "settings.json"
    r = _run_cli("--config", str(cfg), "config", "set", "default_model.base_url", "https://api.example.com/v1")
    assert r.returncode == 0
    assert "已设置" in r.stdout
    data = json.loads(cfg.read_text(encoding="utf-8"))
    assert data["default_model"]["base_url"] == "https://api.example.com/v1"
    # 默认值也在
    assert data["workflow"]["max_review_rounds"] == 3


def test_cli_config_set_unknown_field(tmp_path):
    cfg = tmp_path / "settings.json"
    r = _run_cli("--config", str(cfg), "config", "set", "default_model.hacker", "x")
    assert r.returncode == 1
    assert "未知" in r.stdout
    assert "Traceback" not in r.stderr


def test_cli_config_set_bad_type(tmp_path):
    cfg = tmp_path / "settings.json"
    r = _run_cli("--config", str(cfg), "config", "set", "workflow.max_review_rounds", "abc")
    assert r.returncode == 1
    assert "整数" in r.stdout or "数字" in r.stdout
    assert "Traceback" not in r.stderr


def test_cli_config_set_sensitive_rejected(tmp_path):
    cfg = tmp_path / "settings.json"
    r = _run_cli("--config", str(cfg), "config", "set", "default_model.api_key", "sk-x")
    assert r.returncode == 1
    assert "敏感" in r.stdout or "拒绝" in r.stdout


def test_cli_config_set_key_when_no_keyring(tmp_path):
    """keyring 后端不可用(NOVEL_DISABLE_KEYRING=1)→ set-key 清晰报错 exit 1, 无 traceback, 不含 Key。"""
    import os
    env = dict(os.environ)
    env["NOVEL_DISABLE_KEYRING"] = "1"
    r = subprocess.run(
        [sys.executable, str(PROJECT_ROOT / "adapters" / "cli" / "main.py"),
         "--config", str(tmp_path / "settings.json"), "config", "set-key", "test-ref"],
        capture_output=True, text=True, cwd=PROJECT_ROOT, input="sk-supersecret-value\n", env=env,
    )
    assert r.returncode == 1
    assert "SecretStore" in r.stdout or "不可用" in r.stdout
    assert "sk-supersecret-value" not in r.stdout  # Key 绝不回显
    assert "Traceback" not in r.stderr
