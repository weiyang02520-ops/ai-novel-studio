"""Local mock HTTP server(测试用; 仅 localhost, 不访问互联网)。

可模拟:
- non-stream JSON 响应(含 usage)
- SSE 流式(完整一次写完 / 中途断连接)
- 任意 HTTP status + body(HTML/JSON)
- 响应队列(重试测试: 503 → 200)
- Authorization 检查(必须等于 Bearer <key> / 必须不存在)
并记录所有请求(method/path/headers/body)供断言。
"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Optional


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def do_POST(self):
        mock = self.server.mock
        length = int(self.headers.get("Content-Length") or 0)
        body_raw = self.rfile.read(length) if length else b""
        body_json = None
        try:
            body_json = json.loads(body_raw.decode("utf-8")) if body_raw else None
        except json.JSONDecodeError:
            pass
        req = {
            "method": self.command,
            "path": self.path,
            "headers": {k: v for k, v in self.headers.items()},
            "body_raw": body_raw,
            "body_json": body_json,
        }
        with mock.lock:
            mock.requests.append(req)

        auth = self.headers.get("Authorization")
        if mock.require_auth is not None:
            if auth != f"Bearer {mock.require_auth}":
                self._reply(401, {"error": {"message": "invalid api key"}})
                return
        if mock.forbid_auth and auth:
            with mock.lock:
                mock.auth_violation = True
            self._reply(400, {"error": {"message": "authorization must be absent"}})
            return

        with mock.lock:
            pending = mock.responses.pop(0) if mock.responses else None

        if mock.before_response is not None:
            mock.before_response(req, len(mock.requests))

        if pending is not None:
            status, payload = pending
            if isinstance(payload, str) and payload.startswith("data:"):
                self._raw_reply(status, payload.encode("utf-8"), "text/event-stream")
            elif isinstance(payload, str) and payload.lstrip().startswith("<"):
                self._raw_reply(status, payload.encode("utf-8"), "text/html; charset=utf-8")
            else:
                self._reply(status, payload)
            return

        if mock.stream_mode is not None:
            self._stream(mock)
            return

        self._reply(200, {
            "id": "mock-1",
            "object": "chat.completion",
            "model": "mock-model",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": "你好"},
                         "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 7, "total_tokens": 12},
        })

    def _stream(self, mock: "MockServer"):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()
        try:
            for line in mock.stream_chunks:
                payload = f"data: {line}\n\n".encode("utf-8")
                self.wfile.write(f"{len(payload):X}\r\n".encode() + payload + b"\r\n")
                self.wfile.flush()
            if mock.stream_mode == "interrupt":
                self.connection.close()
                return
            self.wfile.write(b"0\r\n\r\n")
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _raw_reply(self, status: int, payload: bytes, content_type: str):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _reply(self, status: int, payload: Any):
        if isinstance(payload, (dict, list)):
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self._raw_reply(status, body, "application/json")
        else:
            body = str(payload).encode("utf-8")
            self._raw_reply(status, body, "text/plain; charset=utf-8")


class MockServer:
    """线程 localhost HTTP mock server。"""

    def __init__(self):
        self.requests: list[dict[str, Any]] = []
        self.responses: list[tuple[int, Any]] = []
        self.require_auth: Optional[str] = None
        self.forbid_auth: bool = False
        self.auth_violation: bool = False
        self.before_response = None  # optional test hook(request, request_count)
        self.stream_mode: Optional[str] = None  # None | "full" | "interrupt"
        self.stream_chunks: list[str] = []
        self.lock = threading.Lock()
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self.httpd.mock = self
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    def start(self) -> "MockServer":
        self.thread.start()
        return self

    def stop(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}/v1"

    def last_body_json(self, i: int = -1) -> Any:
        return self.requests[i]["body_json"]

    def last_auth(self, i: int = -1) -> Optional[str]:
        return self.requests[i]["headers"].get("Authorization")

    def request_count(self) -> int:
        return len(self.requests)
