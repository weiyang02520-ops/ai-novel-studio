"""测试用 Fake(生产可 import, 不依赖 pytest)。M3 可大量复用 FakeProvider。

- FakeProvider: 返回文本 / tool_calls / 抛 ProviderError / 流式 chunks
- FakeTransport: 模拟 HTTP status/headers/JSON/SSE/timeout/network failure
"""
from __future__ import annotations

from typing import Any, Iterator, Optional

from llm.provider import BaseProvider, ProviderError
from llm.transport import SSEStream, TransportError, TransportHTTPError, TransportResponse
from llm.types import ChatChunk, ChatMessage, ChatResult, ToolCall, Usage


class FakeProvider(BaseProvider):
    """可脚本化行为的 Provider(供测试与 M3 开发)。"""

    def __init__(self, config: Any, *, reply_text: str = "OK",
                 tool_calls: Optional[list[ToolCall]] = None,
                 error: Optional[ProviderError] = None,
                 stream_chunks: Optional[list[ChatChunk]] = None,
                 stream_error: Optional[ProviderError] = None):
        super().__init__(config)
        self.reply_text = reply_text
        self.tool_calls = tool_calls
        self.error = error
        self.stream_chunks = stream_chunks
        self.stream_error = stream_error
        self.last_messages: Optional[list[ChatMessage]] = None

    def chat(self, messages: list[ChatMessage], *, temperature: Optional[float] = None,
             tools: Optional[list[dict[str, Any]]] = None) -> ChatResult:
        self.last_messages = messages
        if self.error is not None:
            raise self.error
        usage = Usage.estimated(10, self.estimate_tokens(self.reply_text))
        return ChatResult(text=self.reply_text, tool_calls=self.tool_calls,
                          usage=usage, model=self.config.model)

    def stream_chat(self, messages: list[ChatMessage], *, temperature: Optional[float] = None,
                    tools: Optional[list[dict[str, Any]]] = None) -> Iterator[ChatChunk]:
        self.last_messages = messages
        if self.stream_error is not None:
            raise self.stream_error
        if self.stream_chunks is not None:
            for c in self.stream_chunks:
                yield c
            return
        yield ChatChunk(kind="text", text=self.reply_text)


class FakeTransport:
    """模拟 HTTP transport: 响应队列 + 请求记录。"""

    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        self.post_responses: list[Any] = []  # (status, headers, body_bytes) | Exception
        self.stream_lines: list[str] = []
        self.stream_transport_error: Optional[TransportError] = None
        self.stream_http_error: Optional[TransportHTTPError] = None
        self.close_called = False

    # 便捷构建

    def add_response(self, status: int = 200, body: Any = None, *, headers: Optional[dict[str, str]] = None) -> None:
        if isinstance(body, (dict, list)):
            body_bytes = __import__("json").dumps(body).encode("utf-8")
        elif isinstance(body, str):
            body_bytes = body.encode("utf-8")
        elif body is None:
            body_bytes = b""
        else:
            body_bytes = body
        self.post_responses.append((status, headers or {}, body_bytes))

    def add_exception(self, e: Exception) -> None:
        self.post_responses.append(e)

    # ── transport 接口 ───────────────────────────────────

    def post_json(self, url: str, headers: dict[str, str], payload: dict[str, Any]) -> TransportResponse:
        self.requests.append({"url": url, "headers": dict(headers), "payload": payload})
        if not self.post_responses:
            raise AssertionError("FakeTransport.post_json 无预设响应")
        item = self.post_responses.pop(0)
        if isinstance(item, Exception):
            raise item
        status, h, body = item
        return TransportResponse(status_code=status, headers=dict(h), body=body, url=url)

    def request_count(self) -> int:
        return len(self.requests)

    def stream_sse(self, url: str, headers: dict[str, str], payload: dict[str, Any]) -> SSEStream:
        self.requests.append({"url": url, "headers": dict(headers), "payload": payload})
        if self.stream_http_error is not None:
            raise self.stream_http_error
        if self.stream_transport_error is not None:
            raise self.stream_transport_error
        return SSEStream(iter(list(self.stream_lines)), close_fn=lambda: None)

    def close(self) -> None:
        self.close_called = True
