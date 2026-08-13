"""Provider-independent, prose-only streaming Writer runner."""
from __future__ import annotations

import dataclasses
import json
from typing import Callable, Optional

from core.generation import GenerationWorkspace
from llm.provider import BaseProvider, ProviderError
from llm.types import ChatMessage, Usage

HARD_MAX_GENERATED_CHARS = 200_000


class WriterError(Exception):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(f"{code}: {message}")


@dataclasses.dataclass
class WriterRequest:
    project: object
    chapter: int
    title: str
    task_card: object
    context_plan: object
    target_chars: int
    mode: str
    existing_text: str = ""
    additional_instruction: str = ""
    base_revision: str = "ABSENT"
    provenance_task_hash: str = ""


@dataclasses.dataclass
class WriterResult:
    text: str
    model: str
    usage: Optional[Usage]
    finish_reason: str
    generation_state: str
    chars: int
    estimated_tokens: int
    context_hash: str
    task_hash: str
    interrupted_error: str = ""


class WriterRunner:
    def __init__(self, provider: BaseProvider, system_prompt: str):
        self.provider = provider
        self.system_prompt = system_prompt

    def run(
        self,
        req: WriterRequest,
        *,
        rendered_context: str,
        on_text_delta: Callable[[str], None] | None = None,
        workspace: GenerationWorkspace | None = None,
        stream: bool = True,
    ) -> WriterResult:
        task_json = json.dumps(req.task_card.to_dict(), ensure_ascii=False, sort_keys=True)
        mode_instruction = {
            "new": "从头撰写本章。",
            "rewrite": "完整改写本章；当前草稿已作为 DATA 提供。",
            "continue": "只续写后文，不要重写已有正文；正文尾部已作为 DATA 提供。",
            "resume": "只续写中断位置之后的正文；此前尾部已作为 DATA 提供。",
        }.get(req.mode, "撰写本章正文。")
        user = (
            f"TASK_CARD:\n{task_json}\n\n{rendered_context}\n模式要求：{mode_instruction}"
            f"\n附加要求：{req.additional_instruction}"
        )
        messages = [
            ChatMessage("system", self.system_prompt),
            ChatMessage("user", user),
        ]
        chunks: list[str] = []
        generated_chars = 0
        finish = ""
        usage = None
        state = "complete"
        interrupted = ""
        if not stream:
            response = self.provider.chat(messages, tools=None)
            if response.tool_calls:
                raise WriterError("WRITER_PROTOCOL_ERROR", "Writer emitted a tool call")
            text = response.text or ""
            if not text:
                raise WriterError("EMPTY_WRITER_OUTPUT", "no prose")
            if len(text) > HARD_MAX_GENERATED_CHARS:
                text = text[:HARD_MAX_GENERATED_CHARS]
                state, finish = "truncated", "hard_limit"
            else:
                finish = response.finish_reason or "unknown"
                state = "truncated" if finish == "length" else "complete"
            if workspace:
                workspace.append(text)
            return WriterResult(
                text=text, model=response.model or getattr(self.provider.config, "model", ""),
                usage=response.usage, finish_reason=finish, generation_state=state,
                chars=len(text), estimated_tokens=BaseProvider.estimate_tokens(text),
                context_hash=req.context_plan.context_hash,
                task_hash=req.provenance_task_hash or req.task_card.task_hash,
            )
        try:
            for chunk in self.provider.stream_chat(messages, tools=None):
                if chunk.kind == "text" and chunk.text:
                    room = HARD_MAX_GENERATED_CHARS - generated_chars
                    if room <= 0:
                        state, finish = "truncated", "hard_limit"
                        break
                    delta = chunk.text[:room]
                    if workspace:
                        workspace.append(delta)
                    chunks.append(delta)
                    generated_chars += len(delta)
                    if on_text_delta:
                        on_text_delta(delta)
                    if len(delta) < len(chunk.text):
                        state, finish = "truncated", "hard_limit"
                        break
                elif chunk.kind == "finish":
                    finish = chunk.finish_reason
                elif chunk.kind == "usage":
                    usage = chunk.usage
                elif chunk.kind == "tool_call":
                    raise WriterError("WRITER_PROTOCOL_ERROR", "Writer emitted a tool call")
        except KeyboardInterrupt:
            # The partial is already flushed. The CLI owns exit code 130.
            raise
        except WriterError:
            # Protocol violations are hard failures. The already-flushed partial is
            # retained for inspection, but must not be presented as resumable success.
            raise
        except ProviderError as exc:
            if chunks:
                interrupted = getattr(exc, "code", type(exc).__name__)
                state = "interrupted"
            else:
                raise

        text = "".join(chunks)
        if not text:
            raise WriterError("EMPTY_WRITER_OUTPUT", "no prose")
        if finish == "length":
            state = "truncated"
        elif state not in ("interrupted", "truncated"):
            state = "complete"
        return WriterResult(
            text=text,
            model=getattr(self.provider.config, "model", ""),
            usage=usage,
            finish_reason=finish or "unknown",
            generation_state=state,
            chars=len(text),
            estimated_tokens=BaseProvider.estimate_tokens(text),
            context_hash=req.context_plan.context_hash,
            task_hash=req.provenance_task_hash or req.task_card.task_hash,
            interrupted_error=interrupted,
        )
