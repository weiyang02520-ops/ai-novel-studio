"""Provider 工厂: config + SecretStore → Provider(当前仅 openai_compatible)。"""
from __future__ import annotations

from typing import Any

from llm.provider import BaseProvider, CONFIG_ERROR, ProviderError, UNSUPPORTED_PROVIDER


def create_provider(config: Any, secret_store: Any = None, transport: Any = None) -> BaseProvider:
    """按 ModelConfig 构建 Provider。

    未知 provider → ProviderError(UNSUPPORTED_PROVIDER), 绝不偷偷用 OpenAI-compatible 发送。
    base_url → 写前/运行前统一 validate_provider_base_url(安全边界不只在 CLI)。
    """
    provider_name = (config.provider or "").strip()
    if not provider_name:
        raise ProviderError(CONFIG_ERROR, "provider 未配置(应为 openai_compatible)。")
    if provider_name != "openai_compatible":
        raise ProviderError(
            UNSUPPORTED_PROVIDER,
            f"不支持的 provider: {provider_name!r}。当前仅支持: openai_compatible。")
    if not (config.base_url or "").strip():
        raise ProviderError(CONFIG_ERROR, "base_url 未配置。请运行: config set default_model.base_url <BASE_URL>")
    if not (config.model or "").strip():
        raise ProviderError(CONFIG_ERROR, "model 未配置。请运行: config set default_model.model <MODEL>")

    from core.config import validate_provider_base_url
    url_err = validate_provider_base_url(config.base_url)
    if url_err:
        raise ProviderError(CONFIG_ERROR, f"base_url 无效: {url_err}")

    from llm.openai_compatible import OpenAICompatibleProvider
    return OpenAICompatibleProvider(config, secret_store, transport)
