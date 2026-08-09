"""配置系统(Core 层, 无 UI 依赖)。

- settings.json 只保存非敏感配置(provider/base_url/model/temperature/capabilities/secret_reference)
- 真实 API Key 经 SecretStore(见 llm/secret_store.py)
- config validate 只做本地校验, 不联网(联网测试在 M2 的 config test-provider)
- 文件损坏/类型错误 → 抛 ConfigError(禁止裸 traceback 到用户)
"""
from __future__ import annotations

import json
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


class ConfigError(Exception):
    """配置错误(文件损坏/类型错误/未知字段)。消息面向用户, 不含堆栈。"""

    def __init__(self, message: str, *, path: Optional[Path] = None):
        self.path = path
        prefix = f"[配置错误] {message}"
        if path is not None:
            prefix += f" (文件: {path})"
        super().__init__(prefix)


def _as_float(value: Any, field_path: str, path: Path) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        raise ConfigError(f"'{field_path}' 需要是数字, 实际: {value!r}", path=path)


def _as_int(value: Any, field_path: str, path: Path) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        raise ConfigError(f"'{field_path}' 需要是整数, 实际: {value!r}", path=path)


def _as_bool(value: Any, field_path: str, path: Path) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        v = value.strip().lower()
        if v in ("true", "1", "yes", "on"):
            return True
        if v in ("false", "0", "no", "off"):
            return False
    raise ConfigError(f"'{field_path}' 需要是布尔值(true/false), 实际: {value!r}", path=path)


def _as_dict(value: Any, field_path: str, path: Path) -> dict:
    if not isinstance(value, dict):
        raise ConfigError(f"'{field_path}' 需要是对象(JSON object), 实际类型: {type(value).__name__}", path=path)
    return value


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
    def from_dict(cls, d: Any, path: Path, label: str) -> "ModelConfig":
        d = _as_dict(d, f"{label}", path)
        caps = d.get("capabilities")
        caps = _as_dict(caps, f"{label}.capabilities", path) if caps is not None else {}
        return cls(
            provider=str(d.get("provider", "openai_compatible")),
            base_url=str(d.get("base_url") or "").strip(),
            model=str(d.get("model") or "").strip(),
            temperature=_as_float(d.get("temperature", 0.8), f"{label}.temperature", path),
            tool_calls=_as_bool(caps.get("tool_calls", True), f"{label}.capabilities.tool_calls", path),
            vision=_as_bool(caps.get("vision", False), f"{label}.capabilities.vision", path),
            max_context_tokens=_as_int(caps.get("max_context_tokens", 128000), f"{label}.capabilities.max_context_tokens", path),
            secret_reference=str(d.get("secret_reference") or "").strip(),
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


# 允许通过 config set 修改的字段白名单: 字段路径 → 类型
SETTABLE_FIELDS: dict[str, type] = {
    "default_model.provider": str,
    "default_model.base_url": str,
    "default_model.model": str,
    "default_model.temperature": float,
    "default_model.secret_reference": str,
    "workflow.max_review_rounds": int,
    "workflow.max_tool_calls_per_turn": int,
    "history.max_snapshots": int,
    "context.reserve_output_tokens": int,
    "context.max_recent_chapters": int,
    "context.max_recent_text_chars": int,
    "auto_accept": bool,
}
# 拒绝的敏感字段(secret_reference 除外)
BLOCKED_FIELDS = {"api_key", "token", "password", "secret", "credential"}


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

    # ── 加载/保存 ─────────────────────────────────────────

    @classmethod
    def load(cls, path: Path) -> "Settings":
        """加载配置。

        - 文件不存在 → 返回默认配置(不写文件)
        - JSON 语法错误 / 结构错误 / 类型错误 → ConfigError
        """
        if not path.exists():
            return cls._from_dict({}, path)

        try:
            raw_text = path.read_text(encoding="utf-8")
        except OSError as e:
            raise ConfigError(f"无法读取配置文件: {e}", path=path)
        try:
            raw = json.loads(raw_text)
        except json.JSONDecodeError as e:
            raise ConfigError(f"JSON 语法错误: 第 {e.lineno} 行第 {e.colno} 列: {e.msg}", path=path)

        return cls._from_dict(raw, path)

    @classmethod
    def _from_dict(cls, raw: Any, path: Path) -> "Settings":
        raw = _as_dict(raw, "根节点", path)
        default = raw.get("default_model", {})
        models_raw = raw.get("models")
        if models_raw is None:
            models_raw = {}
        models_raw = _as_dict(models_raw, "models", path)
        models = {
            k: ModelConfig.from_dict(v, path, f"models.{k}")
            for k, v in models_raw.items()
        }
        return cls(
            default_model=ModelConfig.from_dict(default, path, "default_model"),
            models=models,
            context=_as_dict(raw.get("context", {}), "context", path),
            workflow=_as_dict(raw.get("workflow", {}), "workflow", path),
            history=_as_dict(raw.get("history", {}), "history", path),
            auto_accept=_as_bool(raw.get("auto_accept", False), "auto_accept", path),
            path=path,
        )

    def save(self) -> None:
        """保存配置: 默认值 + 当前值合并, 保证新配置也带全部默认字段。"""
        if self.path is None:
            raise ConfigError("settings path 未设置")
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            raise ConfigError(f"无法创建配置目录: {e}", path=self.path)

        data = _deep_merge_defaults({
            "default_model": self.default_model.to_dict(),
            "models": {k: v.to_dict() for k, v in self.models.items()},
            "context": self.context,
            "workflow": self.workflow,
            "history": self.history,
            "auto_accept": self.auto_accept,
        })
        try:
            self.path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError as e:
            raise ConfigError(f"无法写入配置文件: {e}", path=self.path)

    # ── 设置值(白名单 + 类型转换) ─────────────────────────

    @classmethod
    def _convert_value(cls, field_path: str, value: str, typ: type, path: Path) -> Any:
        if typ is str:
            return str(value)
        if typ is float:
            return _as_float(value, field_path, path)
        if typ is int:
            return _as_int(value, field_path, path)
        if typ is bool:
            return _as_bool(value, field_path, path)
        raise ConfigError(f"不支持的类型: {typ}", path=path)

    def set_value(self, dotted_key: str, value: Any) -> None:
        """按点路径设置白名单字段(带类型转换)。

        未知字段 → ConfigError; 敏感字段(api_key/token/password/secret/credential)→ 拒绝。
        """
        parts = dotted_key.split(".")
        # 敏感字段拦截(整个路径中任一中间段命中)
        for seg in parts:
            if seg in BLOCKED_FIELDS:
                raise ConfigError(
                    f"拒绝通过 config set 修改敏感字段 '{dotted_key}'(使用 config set-key 写入 SecretStore)"
                )
        # models.<role>.<field>: 允许角色级模型配置(字段白名单 = default_model 白名单去前缀)
        if len(parts) == 3 and parts[0] == "models":
            role, sub = parts[1], parts[2]
            allowed_role_fields = {k.split(".", 1)[1] for k in SETTABLE_FIELDS if k.startswith("default_model.")}
            if sub not in allowed_role_fields:
                raise ConfigError(f"未知或不允许的字段 '{dotted_key}'。models.<role> 允许: {sorted(allowed_role_fields)}")
            if role not in self.models:
                self.models[role] = ModelConfig()
            typ = {"provider": str, "base_url": str, "model": str,
                   "temperature": float, "secret_reference": str}[sub]
            path = self.path or Path(".")
            converted = self._convert_value(dotted_key, str(value), typ, path)
            setattr(self.models[role], sub, converted)
            self.save()
            return
        if dotted_key not in SETTABLE_FIELDS:
            raise ConfigError(
                f"未知或不允许的字段 '{dotted_key}'。允许: {sorted(SETTABLE_FIELDS)}"
            )

        typ = SETTABLE_FIELDS[dotted_key]
        path = self.path or Path(".")
        converted = self._convert_value(dotted_key, str(value), typ, path)

        if parts[0] == "default_model" and len(parts) >= 2:
            setattr(self.default_model, parts[1], converted)
        elif parts[0] == "auto_accept":
            self.auto_accept = converted
        else:
            first = parts[0]
            if not hasattr(self, first) or not isinstance(getattr(self, first), dict):
                raise ConfigError(f"未知顶层字段 '{first}'", path=path)
            node: Any = getattr(self, first)
            for p in parts[1:-1]:
                node = node.setdefault(p, {})
            node[parts[-1]] = converted
        self.save()


def _deep_merge_defaults(data: dict[str, Any]) -> dict[str, Any]:
    """把 DEFAULT_SETTINGS 与当前值深度合并(当前值优先, 默认值补齐缺失)。"""
    def merge(base: Any, over: Any) -> Any:
        if isinstance(base, dict) and isinstance(over, dict):
            out = dict(base)
            for k, v in over.items():
                out[k] = merge(base.get(k), v) if k in base else v
            return out
        return over if over is not None else base

    return merge(DEFAULT_SETTINGS, data)


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
