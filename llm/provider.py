"""BaseProvider + ProviderError(Provider 无关, 无 UI 依赖)。

- BaseProvider 定义 chat / stream_chat 统一接口
- M3 Agent Runtime 只依赖 BaseProvider + llm/types 内部模型
- ProviderError 是唯一对外错误类型; message 必须安全(不含 Key/URL query/header)
"""
from __future__ import annotations

from typing import Any, Iterator, Optional

from core.config import ModelConfig
from llm.types import ChatChunk, ChatMessage, ChatResult

# ── 错误码 ────────────────────────────────────────────────

AUTH_ERROR = "AUTH_ERROR"
PERMISSION_ERROR = "PERMISSION_ERROR"
RATE_LIMIT = "RATE_LIMIT"
MODEL_NOT_FOUND = "MODEL_NOT_FOUND"
CONTEXT_TOO_LONG = "CONTEXT_TOO_LONG"
BAD_REQUEST = "BAD_REQUEST"
ENDPOINT_ERROR = "ENDPOINT_ERROR"
NOT_FOUND = "NOT_FOUND"
TIMEOUT = "TIMEOUT"
NETWORK_ERROR = "NETWORK_ERROR"
SERVER_ERROR = "SERVER_ERROR"
MALFORMED_RESPONSE = "MALFORMED_RESPONSE"
EMPTY_RESPONSE = "EMPTY_RESPONSE"
STREAM_INTERRUPTED = "STREAM_INTERRUPTED"
UNSUPPORTED_PROVIDER = "UNSUPPORTED_PROVIDER"
CONFIG_ERROR = "CONFIG_ERROR"
HTTP_ERROR = "HTTP_ERROR"
KEY_NOT_CONFIGURED = "KEY_NOT_CONFIGURED"


class ProviderError(Exception):
    """Provider 统一错误。CLI 只处理这一类错误, 不接触 HTTP/JSON 细节。

    code           — 稳定错误码(见上)
    message        — 安全消息(不含 Key / Authorization / URL query / raw body)
    status_code    — 可选 HTTP 状态
    retryable      — 是否允许自动重试
    provider_error_code — 可选, 服务端返回的错误码(不直接透传敏感内容)
    """

    def __init__(self, code: str, message: str, *,
                 status_code: Optional[int] = None,
                 retryable: bool = False,
                 provider_error_code: Optional[str] = None):
        self.code = code
        self.message = message
        self.status_code = status_code
        self.retryable = retryable
        self.provider_error_code = provider_error_code
        super().__init__(message)


class BaseProvider:
    """Provider 统一接口。子类实现 chat / stream_chat。"""

    def __init__(self, config: ModelConfig, secret_store: Any = None):
        self.config = config
        self.secret_store = secret_store

    # ── 接口 ─────────────────────────────────────────────

    def chat(self, messages: list[ChatMessage], *, temperature: Optional[float] = None,
             tools: Optional[list[dict[str, Any]]] = None) -> ChatResult:
        raise NotImplementedError

    def stream_chat(self, messages: list[ChatMessage], *, temperature: Optional[float] = None,
                    tools: Optional[list[dict[str, Any]]] = None) -> Iterator[ChatChunk]:
        raise NotImplementedError

    # ── 通用能力 ─────────────────────────────────────────

    @staticmethod
    def estimate_tokens(text: str) -> int:
        """确定性近似估算(非服务端真实 token)。

        英文 ~4 字符/token(向上取整), 中文等非 ASCII 按 1 字符/token(近似)。
        服务端返回 usage 时优先使用服务端 usage。
        """
        ascii_chars = sum(1 for c in text if ord(c) < 128)
        other_chars = len(text) - ascii_chars
        return max(1, (ascii_chars + 3) // 4 + other_chars)

    @staticmethod
    def estimate_messages_tokens(messages: list[ChatMessage]) -> int:
        """估算整组 messages 的 tokens(含每消息开销)。"""
        total = 4  # 基础开销
        for m in messages:
            total += 4 + BaseProvider.estimate_tokens(m.content)
            if m.tool_calls:
                for tc in m.tool_calls:
                    total += 8 + BaseProvider.estimate_tokens(tc.name) + BaseProvider.estimate_tokens(tc.arguments_json)
            if m.tool_call_id:
                total += 2 + BaseProvider.estimate_tokens(m.tool_call_id)
        return total

    def check_input_within_context(self, messages: list[ChatMessage]) -> None:
        """基于 max_context_tokens 的明显超长本地保护(不联网, 不裁剪)。"""
        if not self.config.max_context_tokens:
            return
        estimated = self.estimate_messages_tokens(messages)
        if estimated > self.config.max_context_tokens:
            raise ProviderError(
                CONFIG_ERROR,
                f"输入过长: 估算约 {estimated} tokens, 超过模型上限 {self.config.max_context_tokens} tokens。"
                f"请缩短输入(不会自动裁剪)。",
                retryable=False,
            )

    def close(self) -> None:
        """释放底层资源(HTTP client)。CLI 结束时调用; 可幂等。"""
