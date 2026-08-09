"""M0 单元测试: 配置系统 + SecretStore。

运行: python -m pytest tests/ -v
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from core.config import ModelConfig, Settings, validate_settings  # noqa: E402
from llm.secret_store import CompositeSecretStore, EnvSecretStore  # noqa: E402


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


class FakeKeyringStore(CompositeSecretStore):
    """CompositeSecretStore 带假 keyring 后端(可写)。"""

    def __init__(self):
        kr = FakeKeyring()
        # 直接用 EnvSecretStore + 假 keyring 构造
        self._env = EnvSecretStore()
        self._keyring = _FakeKR(kr)


class _FakeKR:
    def __init__(self, kr):
        self._kr = kr
        self.available = True

    def get(self, ref):
        return self._kr.get_password("ai-novel-studio", ref)

    def set(self, ref, value):
        self._kr.set_password("ai-novel-studio", ref, value)

    def delete(self, ref):
        self._kr.delete_password("ai-novel-studio", ref)


# ── Settings ────────────────────────────────────────────────

def test_settings_default_and_save(tmp_path):
    p = tmp_path / "settings.json"
    s = Settings.load(p)  # 不存在 → 默认
    assert s.default_model.provider == "openai_compatible"
    s.default_model.base_url = "https://api.deepseek.com/v1"
    s.default_model.model = "deepseek-chat"
    s.set_value("workflow.max_review_rounds", 3)
    s.save()
    s2 = Settings.load(p)
    assert s2.default_model.base_url == "https://api.deepseek.com/v1"
    assert s2.workflow["max_review_rounds"] == 3


def test_settings_set_model_role(tmp_path):
    p = tmp_path / "settings.json"
    s = Settings.load(p)
    s.set_value("models.writer.base_url", "http://127.0.0.1:11434/v1")
    s.set_value("models.writer.model", "qwen3")
    s.save()
    s2 = Settings.load(p)
    assert s2.model_for("writer").base_url == "http://127.0.0.1:11434/v1"
    assert s2.model_for("unknown").model == ""  # 回退 default


# ── validate(不联网) ────────────────────────────────────────

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
    # base_url/model 合法; secret_reference 为空 → 无 error
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
    store = FakeKeyringStore()  # 假 keyring 无此 ref
    issues = validate_settings(s, secret_store=store)
    assert any("ghost-ref" in i.message for i in issues if i.severity == "error")


# ── SecretStore ─────────────────────────────────────────────

def test_secret_store_set_get_delete():
    store = FakeKeyringStore()
    store.set("test-ref", "sk-fake-value")
    assert store.get("test-ref") == "sk-fake-value"
    assert store.exists("test-ref")
    store.delete("test-ref")
    assert not store.exists("test-ref")


def test_secret_store_env_priority(monkeypatch):
    store = FakeKeyringStore()
    store.set("test-ref", "sk-keyring-value")
    monkeypatch.setenv("NOVEL_API_KEY_TEST_REF", "sk-env-value")
    # 环境变量优先于 keyring
    assert store.get("test-ref") == "sk-env-value"
    monkeypatch.delenv("NOVEL_API_KEY_TEST_REF")
    assert store.get("test-ref") == "sk-keyring-value"


def test_env_store_no_write():
    store = EnvSecretStore()
    try:
        store.set("x", "y")
        raised = False
    except NotImplementedError:
        raised = True
    assert raised, "环境变量后端不应支持写入"


# ── CLI 冒烟(通过 subprocess 调用真实 CLI) ──────────────────

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


def test_cli_config_validate_empty(tmp_path, monkeypatch):
    # 用临时配置目录: 默认配置 → base_url 为空 → error 退出码 1
    r = _run_cli("--config", str(tmp_path / "settings.json"), "config", "validate")
    assert r.returncode == 1
    assert "base_url" in r.stdout


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
