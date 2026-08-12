"""M2 演示用本地 mock(OpenAI-compatible, 支持流式/非流式)。仅供 smoke 演示。"""
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n) or b"{}")
        if body.get("stream"):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            for c in ["AI", " Novel", " Studio"]:
                line = f'data: {json.dumps({"choices": [{"delta": {"content": c}}]})}\n\n'
                payload = line.encode("utf-8")
                self.wfile.write(f"{len(payload):X}\r\n".encode() + payload + b"\r\n")
                self.wfile.flush()
            self.wfile.write(b"0\r\n\r\n")
            self.wfile.flush()
        else:
            out = json.dumps({
                "choices": [{"message": {"role": "assistant", "content": "你好，演示成功"},
                             "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 3, "completion_tokens": 4, "total_tokens": 7},
            }).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(out)))
            self.end_headers()
            self.wfile.write(out)


httpd = ThreadingHTTPServer(("127.0.0.1", 9877), H)
httpd.serve_forever()
