"""Provider-independent, non-streaming Reviewer runner."""
from __future__ import annotations

import dataclasses
import json

from llm.provider import BaseProvider
from llm.types import ChatMessage, Usage
from .review_report import ParsedReviewReport, ReviewReport, ReviewReportError, parse_review_report

REVIEW_REPORT_SCHEMA = {
    "chapter": "positive integer", "verdict": "PASS | NEEDS_WORK", "summary": "string <= 5000 chars",
    "issues": [{
        "id": "string", "category": "fixed category enum", "severity": "BLOCKER | MAJOR | MINOR | INFO",
        "title": "string", "description": "string",
        "location": {"line_start": "integer|null", "line_end": "integer|null", "anchor": "string|null"},
        "evidence": "string <= 300 chars", "suggestion": "string",
    }],
    "strengths": "list[string], max 5", "task_fulfillment": "string",
    "continuity_assessment": "string", "style_assessment": "string", "logic_assessment": "string",
    "confidence": "number 0..1", "source": "string",
}


class ReviewerError(Exception):
    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")


@dataclasses.dataclass(frozen=True)
class ReviewRequest:
    project: object
    chapter: int
    draft_revision: str
    context_plan: object
    instruction: str = ""
    draft_line_count: int | None = None


@dataclasses.dataclass(frozen=True)
class ReviewerResult:
    report: ReviewReport
    model: str
    usage: Usage | None
    context_hash: str
    draft_revision: str
    repaired: bool = False
    normalized: bool = False


class ReviewerRunner:
    def __init__(self, provider: BaseProvider, system_prompt: str):
        self.provider = provider
        self.system_prompt = system_prompt

    @staticmethod
    def _validated(response, request: ReviewRequest) -> ParsedReviewReport:
        if response.tool_calls:
            raise ReviewerError("REVIEW_PROTOCOL_ERROR", "Reviewer emitted a tool call")
        if not response.text:
            raise ReviewReportError("EMPTY_REVIEW_OUTPUT")
        parsed = parse_review_report(response.text, draft_line_count=request.draft_line_count)
        if parsed.report.chapter != request.chapter:
            raise ReviewReportError("REVIEW_CHAPTER_MISMATCH")
        return parsed

    def run(self, req: ReviewRequest, *, rendered_context: str) -> ReviewerResult:
        user = (
            f"目标章: {req.chapter}\n用户审稿要求: {req.instruction}\n"
            f"Schema: {json.dumps(REVIEW_REPORT_SCHEMA, ensure_ascii=False, separators=(',', ':'))}\n"
            f"[REVIEW_DATA_BEGIN]\n{rendered_context}\n[REVIEW_DATA_END]\n"
            "只返回一个符合 Schema 的 JSON object。"
        )
        response = self.provider.chat([
            ChatMessage("system", self.system_prompt), ChatMessage("user", user)], tools=None)
        repaired = False
        try:
            parsed = self._validated(response, req)
        except ReviewerError:
            raise
        except ReviewReportError:
            repair = self.provider.chat([
                ChatMessage("system", "只把输入修正为合法 JSON object，不添加解释。"),
                ChatMessage("user", "Schema: " + json.dumps(REVIEW_REPORT_SCHEMA, ensure_ascii=False,
                                                             separators=(",", ":"))
                            + "\n失败结果:\n" + (response.text or "")),
            ], tools=None)
            repaired = True
            try:
                parsed = self._validated(repair, req)
            except (ReviewReportError, ReviewerError) as exc:
                raise ReviewerError("REVIEW_UNVERIFIED", "Reviewer 未返回可验证报告") from exc
            response = repair
        return ReviewerResult(
            report=parsed.report,
            model=response.model or getattr(self.provider.config, "model", ""),
            usage=response.usage,
            context_hash=getattr(req.context_plan, "context_hash", ""),
            draft_revision=req.draft_revision,
            repaired=repaired,
            normalized=parsed.normalized,
        )
