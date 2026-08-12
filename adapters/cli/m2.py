"""M2 CLI 命令(chat / usage / config test-provider / delete-key / key-status)。

只负责: 参数解析 → 调用 Core/Provider → 展示结果。
Provider 异常 → 人类可读安全错误 + exit 1, 无 traceback, 不泄漏 Key。
"""
from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Optional

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.config import ConfigError, Settings  # noqa: E402
from llm.factory import create_provider  # noqa: E402
from llm.provider import (  # noqa: E402
    EMPTY_RESPONSE,
    STREAM_INTERRUPTED,
    ProviderError,
)
from llm.secret_store import SecretStoreError, default_secret_store  # noqa: E402
from llm.types import ChatMessage, Usage  # noqa: E402
from llm.usage import UsageService  # noqa: E402

DEFAULT_USAGE_PATH = PROJECT_ROOT / "data" / "logs" / "usage.jsonl"


def _settings(args) -> Settings:
    return Settings.load(args.config_path)


def _usage_service(args) -> UsageService:
    path = getattr(args, "usage_path", None) or DEFAULT_USAGE_PATH
    return UsageService(Path(path))


def _validate_temperature(value: Optional[float]) -> Optional[str]:
    """--temperature 严格校验: 有限数字且 >= 0。返回错误消息或 None。"""
    if value is None:
        return None
    if isinstance(value, bool):
        return "--temperature 必须是数字"
    import math
    if not isinstance(value, (int, float)) or not math.isfinite(value):
        return "--temperature 必须是有限数字(NaN/Infinity 已拒绝)"
    if value < 0:
        return "--temperature 必须是 >= 0"
    return None


# ── config test-provider(真联网) ──────────────────────────

def cmd_config_test_provider(args) -> int:
    try:
        s = _settings(args)
    except ConfigError as e:
        print(e)
        return 1

    role = getattr(args, "role", None)
    cfg = s.model_for(role)
    if role and role not in s.models:
        print(f"(角色模型 '{role}' 未配置, 使用 default_model)")

    store = default_secret_store()
    try:
        provider = create_provider(cfg, store)
    except ProviderError as e:
        print(f"✗ Provider 配置错误: {e.message}")
        return 1

    # 最小连接测试请求(§53): 不发小说内容
    messages = [
        ChatMessage(role="system", content="You are a connectivity test."),
        ChatMessage(role="user", content="Reply with exactly OK."),
    ]
    started = time.monotonic()
    try:
        result = provider.chat(messages, temperature=0.1)
    except ProviderError as e:
        duration_ms = (time.monotonic() - started) * 1000
        _usage_service(args).record_error(model=cfg.model, error_code=e.code, duration_ms=duration_ms)
        print(f"✗ Provider 连接失败: {e.message}")
        return 1
    finally:
        provider.close()

    duration_ms = (time.monotonic() - started) * 1000
    usage = result.usage
    # 空回复不得当成功(§80)
    if not result.text.strip() and not result.tool_calls:
        print(f"✗ Provider 返回空回复(模型可能未正确响应)")
        return 1

    _usage_service(args).record_success(
        provider=cfg.provider, model=result.model or cfg.model,
        prompt_tokens=usage.prompt_tokens if usage else 0,
        completion_tokens=usage.completion_tokens if usage else 0,
        total_tokens=usage.total_tokens if usage else 0,
        estimated=bool(usage and usage.estimated), duration_ms=duration_ms, stream=False)

    print("✓ Provider reachable")
    print(f"  model: {result.model or cfg.model}")
    print(f"  latency: {duration_ms:.0f} ms")
    preview = (result.text or "").strip()
    print(f"  reply preview: {preview[:80]!r}")
    if usage is not None:
        print(f"  usage: prompt={usage.prompt_tokens} completion={usage.completion_tokens} "
              f"total={usage.total_tokens} estimated={usage.estimated}")
    return 0


# ── config delete-key / key-status ────────────────────────

def cmd_config_delete_key(args) -> int:
    store = default_secret_store()
    try:
        store.delete(args.reference)
    except SecretStoreError as e:
        if e.code == "KEY_NOT_FOUND":
            print(f"Key '{args.reference}' 不存在(无需删除)")
        elif e.code == "BACKEND_UNAVAILABLE":
            print(f"删除失败: 系统凭据管理器不可用")
        else:
            print(f"删除失败: {e}")
        return 1
    print(f"✓ 已删除 Key(reference={args.reference}); 旧值未显示")
    return 0


def cmd_config_key_status(args) -> int:
    store = default_secret_store()
    try:
        exists = store.exists(args.reference)
    except SecretStoreError as e:
        if e.code == "BACKEND_UNAVAILABLE":
            print("backend unavailable")
        else:
            print(f"error({e.code})")
        return 1
    print("configured" if exists else "missing")
    return 0 if exists else 1


# ── chat(Provider → 模型 → 文本) ─────────────────────────

def cmd_chat(args) -> int:
    prompt = (args.prompt or "").strip()
    if not prompt:
        print("错误: 消息不能为空(已拒绝, 不会发送请求)")
        return 1

    try:
        s = _settings(args)
    except ConfigError as e:
        print(e)
        return 1

    role = getattr(args, "role", None)
    cfg = s.model_for(role)
    if role and role not in s.models:
        print(f"(角色模型 '{role}' 未配置, 使用 default_model)")

    temp_err = _validate_temperature(getattr(args, "temperature", None))
    if temp_err:
        print(f"错误: {temp_err}")
        return 1
    temperature = args.temperature

    store = default_secret_store()
    try:
        provider = create_provider(cfg, store)
    except ProviderError as e:
        print(f"错误: {e.message}")
        return 1

    # §137: system(若有) → user; M2 不注入小说上下文/系统 Agent Prompt
    messages: list[ChatMessage] = []
    if getattr(args, "system", None):
        messages.append(ChatMessage(role="system", content=args.system))
    messages.append(ChatMessage(role="user", content=prompt))

    usage_svc = _usage_service(args)
    started = time.monotonic()

    def record_success(usage: Optional[Usage], model: str, stream: bool) -> None:
        duration_ms = (time.monotonic() - started) * 1000
        ok = usage_svc.record_success(
            provider=cfg.provider, model=model,
            prompt_tokens=usage.prompt_tokens if usage else 0,
            completion_tokens=usage.completion_tokens if usage else 0,
            total_tokens=usage.total_tokens if usage else 0,
            estimated=bool(usage and usage.estimated),
            duration_ms=duration_ms, stream=stream)
        if not ok:
            print("(warning: usage 记录写入失败, 不影响本次结果)", file=sys.stderr)

    try:
        if getattr(args, "no_stream", False):
            result = provider.chat(messages, temperature=temperature)
            print(result.text)
            if result.finish_reason == "length":
                print("(输出因长度限制被截断)")
            record_success(result.usage, result.model or cfg.model, stream=False)
            print(f"[model={result.model or cfg.model}, "
                  f"usage: prompt={result.usage.prompt_tokens if result.usage else '?'} "
                  f"completion={result.usage.completion_tokens if result.usage else '?'}"
                  f"{' (estimated)' if result.usage and result.usage.estimated else ''}]")
            return 0

        # ── 流式(默认): 边收到边输出 ──
        text_parts: list[str] = []
        server_usage: Optional[Usage] = None
        finish_reason = "stop"
        try:
            for chunk in provider.stream_chat(messages, temperature=temperature):
                if chunk.kind == "text":
                    text_parts.append(chunk.text)
                    print(chunk.text, end="", flush=True)
                elif chunk.kind == "finish":
                    finish_reason = chunk.finish_reason
                elif chunk.kind == "usage" and chunk.usage is not None:
                    server_usage = chunk.usage
        except ProviderError as e:
            # §36: 已输出部分保留, 不自动从头重试
            duration_ms = (time.monotonic() - started) * 1000
            usage_svc.record_error(model=cfg.model, error_code=e.code, duration_ms=duration_ms)
            print(f"\n[错误: {e.message}]", file=sys.stderr)
            if e.code == STREAM_INTERRUPTED:
                print("(已输出部分内容保留)", file=sys.stderr)
            return 1
        print()
        if finish_reason == "length":
            print("(输出因长度限制被截断)")
        combined = "".join(text_parts)
        usage = server_usage or Usage.estimated_usage(
            provider.estimate_messages_tokens(messages), provider.estimate_tokens(combined))
        record_success(usage, cfg.model, stream=True)
        print(f"[usage: prompt={usage.prompt_tokens} completion={usage.completion_tokens}"
              f"{' (estimated)' if usage.estimated else ''}]")
        return 0
    except ProviderError as e:
        duration_ms = (time.monotonic() - started) * 1000
        usage_svc.record_error(model=cfg.model, error_code=e.code, duration_ms=duration_ms)
        print(f"错误: {e.message}")
        return 1
    finally:
        provider.close()


# ── usage ─────────────────────────────────────────────────

def cmd_usage_summary(args) -> int:
    agg = _usage_service(args).summary()
    print(f"usage 文件: {args.usage_path}")
    print(f"  requests: {agg['requests']}")
    print(f"  prompt tokens: {agg['prompt_tokens']}")
    print(f"  completion tokens: {agg['completion_tokens']}")
    print(f"  total tokens: {agg['total_tokens']}")
    print(f"  estimated requests: {agg['estimated_requests']}")
    print(f"  errors: {agg['errors']}")
    if agg["skipped_malformed"]:
        print(f"  warning: skipped {agg['skipped_malformed']} malformed usage records")
    return 0


def cmd_usage_recent(args) -> int:
    rows = _usage_service(args).recent(args.limit)
    if not rows:
        print("(无 usage 记录)")
        return 0
    for r in rows:
        ts = str(r.get("timestamp", ""))[:19]
        if r.get("success") is True:
            print(f"  {ts} {str(r.get('provider', '')):18s} {str(r.get('model', '')):24s} "
                  f"p={r.get('prompt_tokens', 0)} c={r.get('completion_tokens', 0)} "
                  f"t={r.get('total_tokens', 0)} "
                  f"{'est' if r.get('estimated') else 'exact':4s} {r.get('duration_ms', '')}ms")
        else:
            print(f"  {ts} ERROR code={r.get('error_code', '?')} model={r.get('model', '')}")
    return 0
