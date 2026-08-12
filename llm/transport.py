"""HTTP transport(通用 HTTP, 无厂商 SDK)。

- httpx: timeout(connect/read 分离)、SSE 逐行迭代、错误分类、可测试注入
- follow_redirects=False: 绝不把 Authorization 跨 host 泄漏(§87)
- verify=True: 默认校验 TLS 证书(§86)
- 非 200 / 非 SSE content-type 的流式响应 → TransportHTTPError(由 Provider 映射为安全错误)
"""
from __future__ import annotations

import dataclasses
import time
from typing import Any, Iterator, Optional

APP_VERSION = "0.1.0"
DEFAULT_USER_AGENT = f"AI-Novel-Studio/{APP_VERSION}"


class TransportError(Exception):
    """传输层错误(网络/timeout/连接中断)。kind: network | timeout | interrupted。"""

    def __init__(self, kind: str, message: str):
        self.kind = kind
        super().__init__(message)


class TransportHTTPError(Exception):
    """非 200 或非 SSE 的 HTTP 响应(Provider 负责状态映射与安全消息)。"""

    def __init__(self, status_code: int, body: bytes, content_type: str = ""):
        self.status_code = status_code
        self.body = body
        self.content_type = content_type
        super().__init__(f"HTTP {status_code}")


@dataclasses.dataclass
class TransportResponse:
    status_code: int
    headers: dict[str, str]
    body: bytes
    url: str

    @property
    def text(self) -> str:
        return self.body.decode("utf-8", errors="replace")


class SSEStream:
    """SSE 响应体: 逐行迭代原始行(provider 负责解析 data: 前缀)。

    迭代过程中的 httpx 异常(读取超时/协议错误/连接断开)统一包装为 TransportError,
    供 provider 判断"是否已产出 → 是否可重试"。
    """

    def __init__(self, raw_iter: Iterator[bytes], close_fn=None):
        self._iter = raw_iter
        self._close_fn = close_fn

    def __iter__(self) -> Iterator[bytes]:
        import httpx
        try:
            yield from self._iter
        except httpx.ReadTimeout as e:
            raise TransportError("timeout", f"读取超时: {e}")
        except httpx.StreamError as e:
            raise TransportError("interrupted", f"流式传输中断: {e}")
        except Exception as e:  # 连接断开等其它传输级异常
            raise TransportError("interrupted", f"流式传输中断: {e}")

    def close(self) -> None:
        if self._close_fn is not None:
            try:
                self._close_fn()
            except Exception:
                pass


class HttpTransport:
    """基于 httpx 的生产 transport。"""

    def __init__(self, *, connect_timeout: float = 10.0, read_timeout: float = 120.0,
                 verify_tls: bool = True, user_agent: str = DEFAULT_USER_AGENT):
        import httpx
        self._httpx = httpx
        self._timeout = httpx.Timeout(connect=connect_timeout, read=read_timeout,
                                      write=30.0, pool=30.0)
        self._client = httpx.Client(
            timeout=self._timeout,
            verify=verify_tls,
            follow_redirects=False,  # 安全默认: 不跨 host 自动重定向(防 Authorization 泄漏)
            headers={"User-Agent": user_agent},
        )

    # ── 非流式 ───────────────────────────────────────────

    def post_json(self, url: str, headers: dict[str, str], payload: dict[str, Any]) -> TransportResponse:
        try:
            resp = self._client.post(url, headers=headers, json=payload)
        except self._httpx.TimeoutException as e:
            raise TransportError("timeout", f"请求超时: {e}")
        except self._httpx.TransportError as e:
            raise TransportError("network", f"网络错误: {e}")
        headers_out = {k: v for k, v in resp.headers.items()}
        body = resp.content
        resp.close()
        return TransportResponse(status_code=resp.status_code, headers=headers_out, body=body, url=str(resp.url))

    # ── 流式 ─────────────────────────────────────────────

    def stream_sse(self, url: str, headers: dict[str, str], payload: dict[str, Any]) -> SSEStream:
        try:
            req = self._client.build_request("POST", url, headers=headers, json=payload)
            resp = self._client.send(req, stream=True)
        except self._httpx.TimeoutException as e:
            raise TransportError("timeout", f"请求超时: {e}")
        except self._httpx.TransportError as e:
            raise TransportError("network", f"网络错误: {e}")

        content_type = resp.headers.get("content-type", "")

        if resp.status_code != 200:
            body = resp.read()
            resp.close()
            raise TransportHTTPError(resp.status_code, body, content_type)

        if "text/event-stream" not in content_type.lower():
            # stream=true 但服务返回普通 JSON/HTML(可能是错误) — 按错误体映射
            body = resp.read()
            resp.close()
            raise TransportHTTPError(resp.status_code, body, content_type)

        def close_fn():
            try:
                resp.close()
            except Exception:
                pass

        try:
            return SSEStream((line for line in resp.iter_lines()), close_fn)
        except self._httpx.StreamError as e:
            raise TransportError("interrupted", f"流式传输中断: {e}")


def _sse_lines_to_text(body: bytes) -> list[str]:
    """把完整 SSE body 拆成 data 行(测试/工具用, 不含 [DONE])。"""
    out: list[str] = []
    for raw in body.decode("utf-8", errors="replace").splitlines():
        line = raw.strip()
        if line.startswith("data: "):
            payload = line[len("data: "):]
            if payload == "[DONE]":
                continue
            out.append(payload)
    return out
