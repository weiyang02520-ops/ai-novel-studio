"""M3 演示用本地 mock(OpenAI-compatible + tool-call 响应序列)。仅供 smoke 演示。

请求 1: 返回 tool_call(list_chapters)
请求 2+: 检查 messages 中的 role=tool 内容 → 返回 grounded 回答
"""
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

state = {"count": 0}


class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def do_POST(self):
        state["count"] += 1
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n) or b"{}")
        if state["count"] == 1:
            payload = {
                "choices": [{"index": 0, "message": {
                    "role": "assistant", "content": "",
                    "tool_calls": [{"id": "demo_call_1", "type": "function",
                                    "function": {"name": "list_chapters", "arguments": "{}"}}]},
                    "finish_reason": "tool_calls"}],
                "model": "demo-model",
            }
        else:
            # 检查真实 tool 结果 → grounded 回答
            tool_msgs = [m for m in body.get("messages", []) if m.get("role") == "tool"]
            tool_content = tool_msgs[0].get("content", "") if tool_msgs else ""
            if "第 3 章" in tool_content or '"status": "confirmed"' in tool_content:
                text = "目前已确认到第 3 章。"
            else:
                text = "基于工具数据回答。"
            payload = {
                "choices": [{"message": {"role": "assistant", "content": text}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 25, "completion_tokens": 8, "total_tokens": 33},
                "model": "demo-model",
            }
        out = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)


httpd = ThreadingHTTPServer(("127.0.0.1", 9878), H)
httpd.serve_forever()
