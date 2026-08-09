"""配置系统(Core 层, 无 UI 依赖)。

- settings.json 只保存非敏感配置(provider/base_url/model/temperature/capabilities/secret_reference)
- 真实 API Key 经 SecretStore(见 llm/secret_store.py)
- config validate 只做本地校验, 不联网(联网测试在 M2 的 config test-provider)
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

DEFAULT_SETTINGS = {
    "default_model": {
        "provider": "openai_compatible",
        "base_url": "",
        "model": "",
        "temperature": 0.8,
        "capabilities": {"tool_calls": True, "vision": False, "max_context_tokens": 128000},
        "secret_reference": "",
    },
    "models": {},
    "context": {
        "reserve_output_tokens": 4096,
        "max_recent_chapters": 5,
        "max_recent_text_chars": 3000,
    },
    "workflow": {"max_review_rounds": 3, "max_tool_calls_per_turn": 8},
    "history": {"max_snapshots": 50},
    "auto_accept": False,
}


@dataclass
class ModelConfig:
    """单个模型配置(非敏感部分)。"""

    provider: str = "openai_compatible"
    base_url: str = ""
    model: str = ""
    temperature: float = 0.8
    tool_calls: bool = True
    vision: bool = False
    max_context_tokens: int = 128000
    secret_reference: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ModelConfig":
        caps = d.get("capabilities", {}) if isinstance(d.get("capabilities"), dict) else {}
        return cls(
            provider=d.get("provider", "openai_compatible"),
            base_url=(d.get("base_url") or "").strip(),
            model=(d.get("model") or "").strip(),
            temperature=float(d.get("temperature", 0.8)),
            tool_calls=bool(caps.get("tool_calls", True)),
            vision=bool(caps.get("vision", False)),
            max_context_tokens=int(caps.get("max_context_tokens", 128000)),
            secret_reference=(d.get("secret_reference") or "").strip(),
            extra={k: v for k, v in d.items() if k not in
                   ("provider", "base_url", "model", "temperature", "capabilities", "secret_reference")},
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "base_url": self.base_url,
            "model": self.model,
            "temperature": self.temperature,
            "capabilities": {
                "tool_calls": self.tool_calls,
                "vision": self.vision,
                "max_context_tokens": self.max_context_tokens,
            },
            "secret_reference": self.secret_reference,
            **self.extra,
        }


@dataclass
class Settings:
    """应用设置(settings.json 的模型)。"""

    default_model: ModelConfig
    models: dict[str, ModelConfig] = field(default_factory=dict)
    context: dict[str, Any] = field(default_factory=dict)
    workflow: dict[str, Any] = field(default_factory=dict)
    history: dict[str, Any] = field(default_factory=dict)
    auto_accept: bool = False
    path: Optional[Path] = None

    def model_for(self, role: str | None) -> ModelConfig:
        """按角色取模型(writer/reviewer/...), 缺省回退 default_model。"""
        if role and role in self.models:
            return self.models[role]
        return self.default_model

    @classmethod
    def load(cls, path: Path) -> "Settings":
        if not path.exists():
            return cls(default_model=ModelConfig(), path=path)
        raw = json.loads(path.read_text(encoding="utf-8"))
        default = raw.get("default_model", {})
        models = {
            k: ModelConfig.from_dict(v)
            for k, v in (raw.get("models") or {}).items()
            if isinstance(v, dict)
        }
        return cls(
            default_model=ModelConfig.from_dict(default),
            models=models,
            context=raw.get("context", {}),
            workflow=raw.get("workflow", {}),
            history=raw.get("history", {}),
            auto_accept=bool(raw.get("auto_accept", False)),
            path=path,
        )

    def save(self) -> None:
        if self.path is None:
            raise ValueError("settings path 未设置")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = DEFAULT_SETTINGS | {
            "default_model": self.default_model.to_dict(),
            "models": {k: v.to_dict() for k, v in self.models.items()},
            "context": self.context,
            "workflow": self.workflow,
            "history": self.history,
            "auto_accept": self.auto_accept,
        }
        self.path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def set_value(self, dotted_key: str, value: Any) -> None:
        """按点路径设置值, 如 default_model.base_url / default_model.model / workflow.max_review_rounds。"""
        parts = dotted_key.split(".")
        # 特殊处理: default_model.* / models.<role>.* 映射到 dataclass
        if parts[0] == "default_model" and len(parts) >= 2:
            setattr(self.default_model, parts[1], value)
        elif parts[0] == "models" and len(parts) >= 3:
            role, attr = parts[1], parts[2]
            if role not in self.models:
                self.models[role] = ModelConfig()
            setattr(self.models[role], attr, value)
        else:
            # 顶层字段(context/workflow/history 等 dict)按点路径嵌套设置
            first = parts[0]
            if not hasattr(self, first) or not isinstance(getattr(self, first), dict):
                raise KeyError(f"无法设置 {dotted_key}: 未知顶层字段 '{first}'")
            node: Any = getattr(self, first)
            for p in parts[1:-1]:
                if not isinstance(node, dict):
                    raise KeyError(f"无法设置 {dotted_key}: 路径非 dict")
                node = node.setdefault(p, {})
            if not isinstance(node, dict):
                raise KeyError(f"无法设置 {dotted_key}: 末端非 dict")
            node[parts[-1]] = value
        self.save()


class ValidationIssue:
    def __init__(self, severity: str, message: str):
        self.severity = severity  # "error" | "warning"
        self.message = message

    def __repr__(self) -> str:
        return f"[{self.severity.upper()}] {self.message}"


_URL_RE = re.compile(r"^https?://", re.I)


def validate_settings(s: Settings, secret_store: Any) -> list[ValidationIssue]:
    """本地校验(不联网)。

    检查:
      - Base URL 格式
      - model 是否存在
      - secret_reference 是否存在(或为空时允许无 Key 场景)
      - SecretStore 能否找到 Key(仅存在性)
    返回 issues 列表(空 = 通过)。
    """
    issues: list[ValidationIssue] = []

    def check_model(label: str, cfg: ModelConfig) -> None:
        if not cfg.base_url:
            issues.append(ValidationIssue("error", f"{label}: base_url 为空"))
        elif not _URL_RE.match(cfg.base_url):
            issues.append(ValidationIssue("error", f"{label}: base_url 不是合法 http(s) URL: {cfg.base_url[:40]}"))
        if not cfg.model:
            issues.append(ValidationIssue("error", f"{label}: model 为空"))
        if cfg.secret_reference:
            if secret_store is None:
                issues.append(ValidationIssue("warning", f"{label}: secret_reference 存在但 SecretStore 不可用"))
            elif not secret_store.exists(cfg.secret_reference):
                issues.append(ValidationIssue("error", f"{label}: secret_reference '{cfg.secret_reference}' 在 SecretStore 中找不到 Key"))
        # secret_reference 为空 = 允许(本地 Ollama 等无 Key 场景)

    check_model("default_model", s.default_model)
    for role, cfg in s.models.items():
        check_model(f"models.{role}", cfg)

    return issues
