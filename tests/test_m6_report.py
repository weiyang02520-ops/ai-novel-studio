from __future__ import annotations

import json

import pytest

from agents.definitions import reviewer_agent_def
from agents.review_report import (
    CATEGORIES,
    MAX_REPORT_CHARS,
    ReviewReportError,
    canonical_report_json,
    parse_review_report,
    report_hash,
)
from agents.reviewer import ReviewRequest, ReviewerError, ReviewerRunner
from llm.provider import BaseProvider
from llm.types import ChatResult, ToolCall, Usage
from core.config import ModelConfig


def _payload(**updates):
    issue = {
        "id": "i-1", "category": "STYLE", "severity": "MINOR",
        "title": "重复措辞", "description": "同一信息重复出现。",
        "location": {"line_start": 2, "line_end": 3, "anchor": "雨声"},
        "evidence": "雨声又响了一次。", "suggestion": "删去第二次说明。",
    }
    data = {
        "chapter": 1, "verdict": "PASS", "summary": "整体可用。", "issues": [issue],
        "strengths": ["人物声线清晰"], "task_fulfillment": "完成章纲目标。",
        "continuity_assessment": "未发现冲突。", "style_assessment": "风格稳定。",
        "logic_assessment": "因果成立。", "confidence": 0.8, "source": "reviewer",
    }
    data.update(updates)
    return data


def test_raw_and_fenced_json_are_strict_and_hash_is_canonical():
    raw = json.dumps(_payload(), ensure_ascii=False)
    a = parse_review_report(raw)
    b = parse_review_report(f"```json\n{raw}\n```")
    assert a.report == b.report
    assert a.normalized is False
    assert canonical_report_json(a.report) == canonical_report_json(b.report)
    assert len(report_hash(a.report)) == 64


@pytest.mark.parametrize("mutation", [
    lambda d: d.update(extra=True),
    lambda d: d["issues"][0].update(extra=True),
    lambda d: d["issues"][0]["location"].update(extra=True),
    lambda d: d.update(verdict="MAYBE"),
    lambda d: d["issues"][0].update(severity="CRITICAL"),
    lambda d: d["issues"][0].update(category="MADE_UP"),
    lambda d: d.update(chapter=True),
    lambda d: d.update(confidence=True),
    lambda d: d.update(confidence=1.1),
    lambda d: d.update(strengths=["x"] * 6),
    lambda d: d.update(summary="x" * 5001),
    lambda d: d["issues"][0].update(evidence="x" * 301),
])
def test_invalid_reports_are_rejected(mutation):
    data = _payload()
    mutation(data)
    with pytest.raises(ReviewReportError):
        parse_review_report(json.dumps(data, ensure_ascii=False))


def test_normalizes_lines_deduplicates_orders_and_downgrades_pass():
    major = _payload()["issues"][0] | {
        "id": "major", "severity": "MAJOR", "title": "  逻辑   断裂 ",
        "location": {"line_start": 99, "line_end": 100, "anchor": "x"},
    }
    duplicate = major | {"id": "duplicate", "title": "逻辑 断裂"}
    blocker = major | {"id": "blocker", "severity": "BLOCKER", "title": "设定冲突"}
    parsed = parse_review_report(json.dumps(_payload(issues=[major, duplicate, blocker]), ensure_ascii=False),
                                 draft_line_count=10)
    assert parsed.normalized is True
    assert parsed.report.verdict == "NEEDS_WORK"
    assert [x.severity for x in parsed.report.issues] == ["BLOCKER", "MAJOR"]
    assert parsed.report.issues[1].location.line_start is None
    assert parsed.report.issues[1].location.line_end is None


def test_duplicate_issue_keeps_highest_severity_before_pass_policy():
    minor = _payload()["issues"][0] | {
        "id": "minor", "category": "LOGIC", "severity": "MINOR", "title": "same issue"}
    blocker = minor | {"id": "blocker", "severity": "BLOCKER", "title": " SAME   ISSUE "}
    parsed = parse_review_report(json.dumps(_payload(issues=[minor, blocker]), ensure_ascii=False))
    assert parsed.report.verdict == "NEEDS_WORK"
    assert [(x.id, x.severity) for x in parsed.report.issues] == [("blocker", "BLOCKER")]


def test_issue_limit_is_deterministically_truncated():
    issue = _payload()["issues"][0]
    issues = [issue | {"id": str(i), "title": f"issue {i}"} for i in range(55)]
    parsed = parse_review_report(json.dumps(_payload(issues=issues), ensure_ascii=False))
    assert parsed.normalized is True
    assert len(parsed.report.issues) == 50


def test_reviewer_definition_has_no_tools():
    definition = reviewer_agent_def()
    assert (definition.id, definition.name, definition.model_role) == ("reviewer", "审稿", "reviewer")
    assert definition.tools == []
    assert definition.max_tool_rounds == 0


class FakeProvider(BaseProvider):
    def __init__(self, replies):
        super().__init__(ModelConfig(model="review-model"))
        self.replies = list(replies)
        self.calls = []

    def chat(self, messages, *, temperature=None, tools=None):
        self.calls.append((messages, tools))
        return self.replies.pop(0)

    def stream_chat(self, *args, **kwargs):
        raise AssertionError("Reviewer must not stream")


class Plan:
    context_hash = "ctx"


def test_runner_uses_non_stream_chat_without_tools():
    provider = FakeProvider([ChatResult(json.dumps(_payload()), model="actual", usage=Usage(total_tokens=4))])
    result = ReviewerRunner(provider, "system").run(
        ReviewRequest(object(), 1, "rev", Plan()), rendered_context="numbered draft")
    assert result.report.verdict == "PASS"
    assert result.model == "actual"
    assert result.context_hash == "ctx"
    assert result.draft_revision == "rev"
    assert provider.calls[0][1] is None


def test_runner_repairs_once_with_only_schema_and_failed_result():
    provider = FakeProvider([
        ChatResult("bad output"), ChatResult(json.dumps(_payload()), model="fixed")])
    result = ReviewerRunner(provider, "secret system").run(
        ReviewRequest(object(), 1, "rev", Plan()), rendered_context="secret context")
    assert result.repaired is True
    assert len(provider.calls) == 2
    repair_messages = provider.calls[1][0]
    assert "Schema:" in repair_messages[1].content
    assert "bad output" in repair_messages[1].content
    assert "secret context" not in repair_messages[1].content


def test_repair_schema_contains_all_fixed_enums():
    provider = FakeProvider([ChatResult("bad"), ChatResult(json.dumps(_payload()))])
    ReviewerRunner(provider, "system").run(
        ReviewRequest(object(), 1, "rev", Plan()), rendered_context="context")
    schema = provider.calls[1][0][1].content.split("\n失败结果:", 1)[0]
    assert all(category in schema for category in CATEGORIES)
    assert all(value in schema for value in ("PASS", "NEEDS_WORK", "BLOCKER", "MAJOR", "MINOR", "INFO"))


def test_oversized_response_fails_closed_without_repairing_full_text():
    provider = FakeProvider([ChatResult("x" * (MAX_REPORT_CHARS + 1))])
    with pytest.raises(ReviewerError, match="REVIEW_UNVERIFIED"):
        ReviewerRunner(provider, "system").run(
            ReviewRequest(object(), 1, "rev", Plan()), rendered_context="context")
    assert len(provider.calls) == 1


def test_report_errors_expose_stable_code_separate_from_message():
    with pytest.raises(ReviewReportError) as caught:
        parse_review_report("x" * (MAX_REPORT_CHARS + 1))
    assert caught.value.code == "REVIEW_REPORT_TOO_LARGE"
    assert str(caught.value)


def test_runner_double_malformed_and_protocol_violation_fail_closed():
    provider = FakeProvider([ChatResult("bad"), ChatResult("still bad")])
    with pytest.raises(ReviewerError, match="REVIEW_UNVERIFIED"):
        ReviewerRunner(provider, "system").run(
            ReviewRequest(object(), 1, "rev", Plan()), rendered_context="context")
    tool = FakeProvider([ChatResult("{}", tool_calls=[ToolCall("1", "write", "{}")])])
    with pytest.raises(ReviewerError, match="REVIEW_PROTOCOL_ERROR"):
        ReviewerRunner(tool, "system").run(
            ReviewRequest(object(), 1, "rev", Plan()), rendered_context="context")
