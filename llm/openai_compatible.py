"""OpenAI Chat Completions 兼容 Provider(第一版唯一 Provider)。

目标: 任何符合 OpenAI Chat Completions 接口的服务(OpenAI/DeepSeek/OpenRouter/Ollama 等)。
不绑定厂商 SDK; 只依赖 llm.transport(httpx)。

职责:
- 请求构造(model/messages/temperature/stream/tools)
- SecretStore 解析(KEY_NOT_FOUND → KEY_NOT_CONFIGURED, 不显示 Key)
- 非流式/SSE 流式解析 → 内部类型(ChatResult / ChatChunk)
- HTTP 状态 → ProviderError 统一映射(安全消息, 不泄漏 Key/raw body)
- 重试: 网络/timeout/5xx 最多 1 次; 流式已产出 → 不重试(STREAM_INTERRUPTED)
"""
from __future__ import annotations

import json
import re
import time
from typing import Any, Iterator, Optional

from llm.provider import (
    AUTH_ERROR,
    BAD_REQUEST,
    CONFIG_ERROR,
    CONTEXT_TOO_LONG,
    EMPTY_RESPONSE,
    ENDPOINT_ERROR,
    HTTP_ERROR,
    KEY_NOT_CONFIGURED,
    MALFORMED_RESPONSE,
    MODEL_NOT_FOUND,
    NETWORK_ERROR,
    NOT_FOUND,
    PERMISSION_ERROR,
    RATE_LIMIT,
    SERVER_ERROR,
    STREAM_INTERRUPTED,
    TIMEOUT,
    BaseProvider,
    ProviderError,
)
from llm.secret_store import SecretStoreError
from llm.transport import SSEStream, TransportError, TransportHTTPError, TransportResponse
from llm.types import ChatChunk, ChatMessage, ChatResult, ToolCall, Usage

MAX_ATTEMPTS = 2
BACKOFF_BASE = 0.5  # 秒

# 400 错误安全分类(§29-30)
_MODEL_ERR_RE = re.compile(r"model[^\n]{0,60}(not found|does not exist|unsupported model|invalid model|unknown model)", re.I)
_MODEL_FOUND_RE = re.compile(r"model", re.I)
_NOT_FOUND_PHRASE_RE = re.compile(r"(not found|does not exist|unsupported|invalid model|unknown model)", re.I)
_CONTEXT_HINT_RE = re.compile(r"(context|token limit|maximum context|context_length|context window|too long|exceed)", re.I)

MAX_ERROR_BODY = 600  # §73: 安全截断


def _safe_error_message(body_text: str, content_type: str = "") -> str:
    """从响应体提取安全、截断的消息(不 dump 完整 HTML / 几十 KB body)。"""
    text = (body_text or "").strip()
    if not text:
        return ""
    ct = content_type.lower()
    if "html" in ct or text.lstrip().startswith("<"):
        return "服务返回 HTML 页面(可能是网关/防火墙错误页)"
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            err = data.get("error")
            if isinstance(err, dict) and isinstance(err.get("message"), str):
                return err["message"][:MAX_ERROR_BODY]
            if isinstance(err, str) and err:
                return err[:MAX_ERROR_BODY]
            if isinstance(data.get("message"), str):
                return data["message"][:MAX_ERROR_BODY]
    except json.JSONDecodeError:
        pass
    if len(text) > MAX_ERROR_BODY:
        return text[:MAX_ERROR_BODY] + "..."
    return text


def map_http_error(status_code: int, body_text: str, headers: Optional[dict[str, str]] = None,
                   api_key: Optional[str] = None) -> ProviderError:
    """HTTP 状态 → ProviderError(安全消息, retryable 标记)。

    api_key 已知时: 即使服务端错误体回显了 Key, 也绝不进入消息(§90)。
    """
    headers = headers or {}
    safe = _safe_error_message(body_text, headers.get("content-type", ""))
    if api_key and api_key in safe:
        safe = safe.replace(api_key, "[REDACTED]")

    def detail(extra: str) -> str:
        return f"{extra} {safe[:160]}" if safe else extra

    if status_code == 400:
        if _MODEL_ERR_RE.search(safe) or (_MODEL_FOUND_RE.search(safe) and _NOT_FOUND_PHRASE_RE.search(safe)):
            return ProviderError(MODEL_NOT_FOUND,
                                 detail("模型无效或不存在, 请检查模型名(model 配置)。"), status_code=400)
        if _CONTEXT_HINT_RE.search(safe):
            return ProviderError(CONTEXT_TOO_LONG,
                                 detail("请求超出模型的上下文长度限制, 请缩短输入。"), status_code=400)
        return ProviderError(BAD_REQUEST, detail("请求无效(400)。"), status_code=400)
    if status_code == 401:
        return ProviderError(AUTH_ERROR, "API Key 无效或未授权。请检查 config set-key 配置的 Key。", status_code=401)
    if status_code == 403:
        return ProviderError(PERMISSION_ERROR, "API Key 权限不足、账号/模型权限受限。", status_code=403)
    if status_code == 404:
        return ProviderError(NOT_FOUND, "资源不存在(404)。请检查 Base URL / Endpoint / 模型兼容性。", status_code=404)
    if status_code == 405:
        return ProviderError(ENDPOINT_ERROR,
                             "请求方法/Endpoint 不受支持(405)。请检查 Base URL 是否为 OpenAI-compatible API base(通常以 /v1 结尾)。",
                             status_code=405)
    if status_code == 408:
        return ProviderError(TIMEOUT, "请求超时(408)。", status_code=408, retryable=True)
    if status_code == 409:
        return ProviderError(BAD_REQUEST, detail("请求冲突(409)。"), status_code=409)
    if status_code == 422:
        return ProviderError(BAD_REQUEST, detail("请求无法处理(422)。"), status_code=422)
    if status_code == 429:
        msg = "请求受限, 请稍后重试。"
        retry_after = next((v for k, v in headers.items() if k.lower() == "retry-after"), None)
        if retry_after:
            msg += f" 服务建议 Retry-After: {retry_after} 秒。"
        return ProviderError(RATE_LIMIT, msg, status_code=429)
    if 500 <= status_code <= 599:
        return ProviderError(SERVER_ERROR, detail(f"服务端错误({status_code})。"), status_code=status_code, retryable=True)
    return ProviderError(HTTP_ERROR, f"HTTP {status_code}。", status_code=status_code)


def transport_to_provider_error(e: TransportError) -> ProviderError:
    """传输错误 → ProviderError(安全消息)。"""
    if e.kind == "timeout":
        return ProviderError(TIMEOUT, "请求超时(连接或读取超时)。请检查网络或稍后重试。", retryable=True)
    return ProviderError(NETWORK_ERROR, "网络错误, 无法连接服务。请检查 Base URL 与网络。", retryable=True)


class OpenAICompatibleProvider(BaseProvider):
    def __init__(self, config: Any, secret_store: Any = None, transport: Any = None):
        super().__init__(config, secret_store)
        from llm.transport import HttpTransport
        self._transport = transport if transport is not None else HttpTransport()

    # ── 密钥 ─────────────────────────────────────────────

    def _resolve_secret(self) -> Optional[str]:
        """secret_reference → Key; 空 reference → None(keyless)。错误映射为用户可读消息, 不显示 Key。"""
        ref = self.config.secret_reference
        if not ref:
            return None
        if self.secret_store is None:
            raise ProviderError(CONFIG_ERROR, "secret_reference 已配置但 SecretStore 不可用。", retryable=False)
        try:
            key = self.secret_store.get(ref)
        except SecretStoreError as e:
            if e.code == "KEY_NOT_FOUND":
                raise ProviderError(
                    KEY_NOT_CONFIGURED,
                    f"API Key 尚未配置(reference='{ref}')。请运行: config set-key {ref}",
                    retryable=False,
                )
            if e.code == "BACKEND_UNAVAILABLE":
                raise ProviderError(
                    CONFIG_ERROR,
                    "系统凭据管理器不可用, 无法读取 API Key。请安装 keyring 支持或使用环境变量 NOVEL_API_KEY_<REF>。",
                    retryable=False,
                )
            raise ProviderError(CONFIG_ERROR, "读取 API Key 失败(SecretStore 后端错误)。", retryable=False)
        if not key:
            raise ProviderError(KEY_NOT_CONFIGURED, f"API Key 为空(reference='{ref}')。请重新 config set-key。", retryable=False)
        return key

    # ── URL / 请求构造 ───────────────────────────────────

    def _endpoint(self) -> str:
        base = self.config.base_url.strip()
        if not base:
            raise ProviderError(CONFIG_ERROR, "base_url 未配置。请运行: config set default_model.base_url <BASE_URL>", retryable=False)
        if not base.startswith(("http://", "https://")):
            raise ProviderError(CONFIG_ERROR, f"base_url 必须是 http(s) URL, 实际: {base[:40]}", retryable=False)
        base = base.rstrip("/")  # §14: 末尾 / 不产生 //
        if base.endswith("/chat/completions"):
            return base  # §15: 已填完整 endpoint → 原样使用
        return base + "/chat/completions"

    def _build_headers(self, api_key: Optional[str]) -> dict[str, str]:
        headers: dict[str, str] = {}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        return headers

    def _build_payload(self, messages: list[ChatMessage], temperature: Optional[float],
                       tools: Optional[list[dict[str, Any]]], *, stream: bool) -> dict[str, Any]:
        if not messages:
            raise ProviderError(CONFIG_ERROR, "messages 不能为空", retryable=False)
        payload: dict[str, Any] = {
            "model": self.config.model,
            "messages": [m.to_dict() for m in messages],
            "temperature": temperature if temperature is not None else self.config.temperature,
        }
        if stream:
            payload["stream"] = True
        if tools:
            if not self.config.tool_calls:
                raise ProviderError(
                    CONFIG_ERROR, "capabilities.tool_calls=false, 但请求传入了 tools。", retryable=False)
            payload["tools"] = tools
        return payload

    # ── 非流式 ───────────────────────────────────────────

    def chat(self, messages: list[ChatMessage], *, temperature: Optional[float] = None,
             tools: Optional[list[dict[str, Any]]] = None) -> ChatResult:
        self.check_input_within_context(messages)
        api_key = self._resolve_secret()
        url = self._endpoint()
        headers = self._build_headers(api_key)
        payload = self._build_payload(messages, temperature, tools, stream=False)

        attempts = 0
        while True:
            attempts += 1
            try:
                resp = self._transport.post_json(url, headers, payload)
            except TransportError as e:
                err = transport_to_provider_error(e)
                if err.retryable and attempts < MAX_ATTEMPTS:
                    time.sleep(BACKOFF_BASE * attempts)
                    continue
                raise err
            if resp.status_code == 200:
                return self._parse_chat_response(resp, payload)
            err = map_http_error(resp.status_code, resp.text, resp.headers, api_key=api_key)
            if err.retryable and attempts < MAX_ATTEMPTS:
                time.sleep(BACKOFF_BASE * attempts)
                continue
            raise err

    def _parse_chat_response(self, resp: TransportResponse, payload: dict[str, Any]) -> ChatResult:
        try:
            data = json.loads(resp.text)
        except json.JSONDecodeError:
            raise ProviderError(MALFORMED_RESPONSE, "模型响应不是合法 JSON(可能服务返回了 HTML 错误页)。",
                                status_code=resp.status_code)
        if not isinstance(data, dict):
            raise ProviderError(MALFORMED_RESPONSE, "模型响应根节点不是对象。", status_code=resp.status_code)
        choices = data.get("choices")
        if not isinstance(choices, list) or not choices:
            raise ProviderError(MALFORMED_RESPONSE, "模型响应缺少 choices 列表。", status_code=resp.status_code)
        c0 = choices[0]
        if not isinstance(c0, dict):
            raise ProviderError(MALFORMED_RESPONSE, "choices[0] 不是对象。", status_code=resp.status_code)
        msg = c0.get("message")
        if not isinstance(msg, dict):
            raise ProviderError(MALFORMED_RESPONSE, "choices[0].message 缺失。", status_code=resp.status_code)
        content = msg.get("content")
        if content is None:
            content = ""
        if not isinstance(content, str):
            raise ProviderError(MALFORMED_RESPONSE, "choices[0].message.content 不是字符串。", status_code=resp.status_code)

        tool_calls = self._parse_tool_calls(msg.get("tool_calls"))
        if not content and not tool_calls:
            raise ProviderError(EMPTY_RESPONSE, "模型返回空响应(无正文、无工具调用)。", status_code=resp.status_code)

        finish_reason = c0.get("finish_reason") or "stop"
        if not isinstance(finish_reason, str):
            finish_reason = str(finish_reason)
        usage = self._parse_usage(data.get("usage"), payload=payload, completion_text=content)
        model = data.get("model") or self.config.model
        return ChatResult(text=content, tool_calls=tool_calls, usage=usage,
                          model=str(model), finish_reason=finish_reason)

    # ── 流式 ─────────────────────────────────────────────

    def stream_chat(self, messages: list[ChatMessage], *, temperature: Optional[float] = None,
                    tools: Optional[list[dict[str, Any]]] = None) -> Iterator[ChatChunk]:
        self.check_input_within_context(messages)
        api_key = self._resolve_secret()
        url = self._endpoint()
        headers = self._build_headers(api_key)
        payload = self._build_payload(messages, temperature, tools, stream=True)

        attempts = 0
        while True:
            attempts += 1
            emitted = False
            try:
                sse = self._transport.stream_sse(url, headers, payload)
                try:
                    for event in self._iter_stream_events(sse, payload):
                        emitted = True
                        yield event
                finally:
                    sse.close()
                return
            except TransportError as e:
                if emitted:
                    raise ProviderError(STREAM_INTERRUPTED,
                                        "流式输出中断(已输出部分内容保留, 未从头重试)。", retryable=False)
                err = transport_to_provider_error(e)
                if err.retryable and attempts < MAX_ATTEMPTS:
                    time.sleep(BACKOFF_BASE * attempts)
                    continue
                raise err
            except TransportHTTPError as e:
                if emitted:
                    raise ProviderError(STREAM_INTERRUPTED,
                                        "流式输出中断(已输出部分内容保留, 未从头重试)。", retryable=False)
                if e.status_code == 200:
                    # §46: stream=true 但服务返回非 SSE(普通 JSON/HTML) — 不是可重试错误
                    raise ProviderError(MALFORMED_RESPONSE,
                                        "stream 请求返回了非 SSE 响应(可能服务不支持流式输出, 请尝试 --no-stream)。",
                                        status_code=200)
                err = map_http_error(e.status_code, e.body.decode("utf-8", errors="replace"),
                                     {"content-type": e.content_type}, api_key=api_key)
                if err.retryable and attempts < MAX_ATTEMPTS:
                    time.sleep(BACKOFF_BASE * attempts)
                    continue
                raise err

    def _iter_stream_events(self, sse: SSEStream, payload: dict[str, Any]) -> Iterator[ChatChunk]:
        """解析 SSE 行 → ChatChunk(忽略空行/注释行; 坏 JSON → MALFORMED_RESPONSE)。"""
        tool_buf: dict[int, dict[str, str]] = {}
        for raw in sse:
            line = str(raw).strip()
            if not line or line.startswith(":"):
                continue  # 空行 / SSE 注释行(§40)
            if not line.startswith("data:"):
                continue
            data = line[len("data:"):].strip()
            if data == "[DONE]":
                break
            try:
                obj = json.loads(data)
            except json.JSONDecodeError as e:
                raise ProviderError(MALFORMED_RESPONSE, f"SSE data 行 JSON 损坏: {e.msg}")
            if not isinstance(obj, dict):
                raise ProviderError(MALFORMED_RESPONSE, "SSE data 不是对象。")

            # usage 可能出现在末尾行(choices 为空)或独立行(§44)
            usage_raw = obj.get("usage")
            if isinstance(usage_raw, dict):
                usage = self._parse_usage(usage_raw, payload=payload, completion_text="")
                yield ChatChunk(kind="usage", usage=usage)

            choices = obj.get("choices")
            if not isinstance(choices, list) or not choices:
                continue
            c0 = choices[0]
            if not isinstance(c0, dict):
                continue
            delta = c0.get("delta") or {}
            if not isinstance(delta, dict):
                continue

            content = delta.get("content")
            if isinstance(content, str) and content:
                yield ChatChunk(kind="text", text=content)

            raw_tcs = delta.get("tool_calls")
            if isinstance(raw_tcs, list):
                for rtc in raw_tcs:
                    if not isinstance(rtc, dict):
                        continue
                    index = rtc.get("index", 0)
                    if not isinstance(index, int):
                        index = 0
                    buf = tool_buf.setdefault(index, {"id": "", "name": "", "args": ""})
                    tc_id = rtc.get("id")
                    if isinstance(tc_id, str) and tc_id:
                        buf["id"] = tc_id
                    fn = rtc.get("function")
                    if isinstance(fn, dict):
                        name = fn.get("name")
                        if isinstance(name, str) and name:
                            buf["name"] = name
                        args = fn.get("arguments")
                        if isinstance(args, str) and args:
                            buf["args"] += args
                    yield ChatChunk(kind="tool_call", tool_call_index=index,
                                    tool_call_id=buf["id"], tool_call_name=buf["name"],
                                    tool_call_arguments=buf["args"])

            finish = c0.get("finish_reason")
            if isinstance(finish, str) and finish:
                yield ChatChunk(kind="finish", finish_reason=finish)

    # ── 解析共用 ─────────────────────────────────────────

    @staticmethod
    def _parse_tool_calls(raw: Any) -> Optional[list[ToolCall]]:
        if raw is None:
            return None
        if not isinstance(raw, list):
            raise ProviderError(MALFORMED_RESPONSE, "message.tool_calls 不是列表。")
        out: list[ToolCall] = []
        for tc in raw:
            if not isinstance(tc, dict):
                raise ProviderError(MALFORMED_RESPONSE, "tool_call 不是对象。")
            fn = tc.get("function")
            if not isinstance(fn, dict):
                raise ProviderError(MALFORMED_RESPONSE, "tool_call.function 缺失。")
            out.append(ToolCall(
                id=str(tc.get("id") or ""),
                name=str(fn.get("name") or ""),
                arguments_json=str(fn.get("arguments") or ""),
            ))
        return out

    def _parse_usage(self, raw: Any, *, payload: dict[str, Any], completion_text: str) -> Usage:
        """usage 解析(§82): total 缺 → prompt+completion; usage 整体缺 → estimated。"""
        if isinstance(raw, dict):
            pt, ct = raw.get("prompt_tokens"), raw.get("completion_tokens")
            if not isinstance(pt, int) or isinstance(pt, bool) or not isinstance(ct, int) or isinstance(ct, bool):
                raise ProviderError(MALFORMED_RESPONSE, "usage 字段类型非法。")
            tt = raw.get("total_tokens")
            if not isinstance(tt, int) or isinstance(tt, bool):
                tt = pt + ct
            return Usage(prompt_tokens=pt, completion_tokens=ct, total_tokens=tt, source="provider")
        # usage 缺失 → 本地估算(明确标记 estimated)
        prompt_text = self._payload_prompt_text(payload)
        return Usage.estimated_usage(self.estimate_tokens(prompt_text), self.estimate_tokens(completion_text))

    @staticmethod
    def _payload_prompt_text(payload: dict[str, Any]) -> str:
        parts: list[str] = []
        for m in payload.get("messages", []):
            parts.append(str(m.get("role", "")))
            parts.append(str(m.get("content", "")))
        return "\n".join(parts)

    def close(self) -> None:
        close = getattr(self._transport, "close", None)
        if callable(close):
            close()
