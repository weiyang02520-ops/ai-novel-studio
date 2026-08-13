"""Strict, deterministic Reviewer report value objects and JSON parser."""
from __future__ import annotations

import dataclasses
import hashlib
import json
import math
import re
from typing import Any

VERDICTS = frozenset({"PASS", "NEEDS_WORK"})
SEVERITIES = ("BLOCKER", "MAJOR", "MINOR", "INFO")
CATEGORIES = frozenset({
    "TASK_FULFILLMENT", "CHARACTER", "WORLD", "CONTINUITY", "TIMELINE",
    "LOGIC", "CAUSALITY", "SCENE", "POV", "STYLE", "DIALOGUE", "PACING",
    "REPETITION", "EXPOSITION", "AI_VOICE", "FORESHADOWING", "OTHER",
})
MAX_EVIDENCE_CHARS = 300
MAX_STRENGTHS = 5
MAX_ISSUES = 50
MAX_SUMMARY_CHARS = 5_000
MAX_REPORT_CHARS = 500_000


class ReviewReportError(ValueError):
    pass


def _object(data: Any, name: str, allowed: set[str], required: set[str]) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise ReviewReportError(f"{name} 必须是 JSON object")
    unknown = set(data) - allowed
    missing = required - set(data)
    if unknown:
        raise ReviewReportError(f"{name} unknown fields: {sorted(unknown)}")
    if missing:
        raise ReviewReportError(f"{name} missing fields: {sorted(missing)}")
    return data


def _string(value: Any, name: str, *, nonempty: bool = False, max_chars: int | None = None) -> str:
    if not isinstance(value, str):
        raise ReviewReportError(f"{name} 必须是 string")
    if nonempty and not value.strip():
        raise ReviewReportError(f"{name} 不能为空")
    if max_chars is not None and len(value) > max_chars:
        raise ReviewReportError(f"{name} 超过 {max_chars} chars")
    return value


@dataclasses.dataclass(frozen=True)
class ReviewLocation:
    line_start: int | None
    line_end: int | None
    anchor: str | None

    def __post_init__(self) -> None:
        for name in ("line_start", "line_end"):
            value = getattr(self, name)
            if value is not None and (not isinstance(value, int) or isinstance(value, bool)):
                raise ReviewReportError(f"location.{name} 必须是 integer 或 null")
        if self.anchor is not None and not isinstance(self.anchor, str):
            raise ReviewReportError("location.anchor 必须是 string 或 null")


@dataclasses.dataclass(frozen=True)
class ReviewIssue:
    id: str
    category: str
    severity: str
    title: str
    description: str
    location: ReviewLocation
    evidence: str
    suggestion: str

    def __post_init__(self) -> None:
        for name in ("id", "title", "description", "suggestion"):
            _string(getattr(self, name), f"issue.{name}", nonempty=True)
        _string(self.evidence, "issue.evidence", max_chars=MAX_EVIDENCE_CHARS)
        if self.category not in CATEGORIES:
            raise ReviewReportError(f"invalid category: {self.category!r}")
        if self.severity not in SEVERITIES:
            raise ReviewReportError(f"invalid severity: {self.severity!r}")
        if not isinstance(self.location, ReviewLocation):
            raise ReviewReportError("issue.location 必须是 ReviewLocation")


@dataclasses.dataclass(frozen=True)
class ReviewReport:
    chapter: int
    verdict: str
    summary: str
    issues: tuple[ReviewIssue, ...]
    strengths: tuple[str, ...]
    task_fulfillment: str
    continuity_assessment: str
    style_assessment: str
    logic_assessment: str
    confidence: float
    source: str

    def __post_init__(self) -> None:
        if not isinstance(self.chapter, int) or isinstance(self.chapter, bool) or self.chapter < 1:
            raise ReviewReportError("chapter 必须是正整数")
        if self.verdict not in VERDICTS:
            raise ReviewReportError(f"invalid verdict: {self.verdict!r}")
        _string(self.summary, "summary", nonempty=True, max_chars=MAX_SUMMARY_CHARS)
        for name in ("task_fulfillment", "continuity_assessment", "style_assessment",
                     "logic_assessment", "source"):
            _string(getattr(self, name), name, nonempty=True)
        if not isinstance(self.issues, tuple) or not all(isinstance(x, ReviewIssue) for x in self.issues):
            raise ReviewReportError("issues 必须是 ReviewIssue 数组")
        if not isinstance(self.strengths, tuple) or not all(isinstance(x, str) for x in self.strengths):
            raise ReviewReportError("strengths 必须是 string 数组")
        if len(self.strengths) > MAX_STRENGTHS:
            raise ReviewReportError(f"strengths 最多 {MAX_STRENGTHS} 条")
        if any(not x.strip() for x in self.strengths):
            raise ReviewReportError("strengths 不能包含空字符串")
        if (not isinstance(self.confidence, (int, float)) or isinstance(self.confidence, bool)
                or not math.isfinite(float(self.confidence)) or not 0 <= self.confidence <= 1):
            raise ReviewReportError("confidence 必须是 0.0~1.0 的有限数字")

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)

    @property
    def report_hash(self) -> str:
        return report_hash(self)


@dataclasses.dataclass(frozen=True)
class ParsedReviewReport:
    report: ReviewReport
    normalized: bool = False


LOCATION_FIELDS = {"line_start", "line_end", "anchor"}
ISSUE_FIELDS = {"id", "category", "severity", "title", "description", "location", "evidence", "suggestion"}
REPORT_FIELDS = {
    "chapter", "verdict", "summary", "issues", "strengths", "task_fulfillment",
    "continuity_assessment", "style_assessment", "logic_assessment", "confidence", "source",
}


def _parse_location(data: Any) -> ReviewLocation:
    obj = _object(data, "location", LOCATION_FIELDS, LOCATION_FIELDS)
    return ReviewLocation(obj["line_start"], obj["line_end"], obj["anchor"])


def _parse_issue(data: Any) -> ReviewIssue:
    obj = _object(data, "issue", ISSUE_FIELDS, ISSUE_FIELDS)
    return ReviewIssue(
        id=obj["id"], category=obj["category"], severity=obj["severity"], title=obj["title"],
        description=obj["description"], location=_parse_location(obj["location"]),
        evidence=obj["evidence"], suggestion=obj["suggestion"],
    )


def _normalize_location(location: ReviewLocation, draft_line_count: int | None) -> tuple[ReviewLocation, bool]:
    start, end = location.line_start, location.line_end
    invalid = ((start is None) != (end is None))
    if start is not None and end is not None:
        invalid = invalid or start < 1 or end < start
        if draft_line_count is not None:
            invalid = invalid or end > draft_line_count
    if invalid:
        return ReviewLocation(None, None, location.anchor), True
    return location, False


def _title_key(title: str) -> str:
    return " ".join(title.casefold().split())


def _normalize_issues(issues: list[ReviewIssue], draft_line_count: int | None) -> tuple[tuple[ReviewIssue, ...], bool]:
    changed = False
    unique: dict[tuple[Any, ...], ReviewIssue] = {}
    for issue in issues:
        location, line_changed = _normalize_location(issue.location, draft_line_count)
        changed |= line_changed
        if line_changed:
            issue = dataclasses.replace(issue, location=location)
        key = (issue.category, issue.location.line_start, issue.location.line_end,
               issue.location.anchor, _title_key(issue.title))
        if key in unique:
            changed = True
            continue
        unique[key] = issue
    ordered = sorted(unique.values(), key=lambda x: (
        SEVERITIES.index(x.severity), x.category,
        x.location.line_start if x.location.line_start is not None else 10**12,
        _title_key(x.title), x.id,
    ))
    changed |= ordered != issues
    if len(ordered) > MAX_ISSUES:
        ordered = ordered[:MAX_ISSUES]
        changed = True
    return tuple(ordered), changed


def parse_review_report(text: str, *, draft_line_count: int | None = None) -> ParsedReviewReport:
    if not isinstance(text, str):
        raise ReviewReportError("review result 必须是 string")
    if draft_line_count is not None and (
            not isinstance(draft_line_count, int) or isinstance(draft_line_count, bool) or draft_line_count < 0):
        raise ReviewReportError("draft_line_count 必须是非负整数")
    if len(text) > MAX_REPORT_CHARS + 32:
        raise ReviewReportError("REVIEW_REPORT_TOO_LARGE")
    raw = text.strip()
    match = re.fullmatch(r"```(?:json)?\s*\n?(.*?)\n?```", raw, re.S | re.I)
    if match:
        raw = match.group(1).strip()
    if len(raw) > MAX_REPORT_CHARS:
        raise ReviewReportError("REVIEW_REPORT_TOO_LARGE")
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, RecursionError) as exc:
        raise ReviewReportError("REVIEW_JSON_INVALID") from exc
    obj = _object(data, "report", REPORT_FIELDS, REPORT_FIELDS)
    if not isinstance(obj["issues"], list):
        raise ReviewReportError("issues 必须是 array")
    if not isinstance(obj["strengths"], list):
        raise ReviewReportError("strengths 必须是 array")
    issues, changed = _normalize_issues([_parse_issue(x) for x in obj["issues"]], draft_line_count)
    verdict = obj["verdict"]
    if verdict == "PASS" and any(x.severity in {"BLOCKER", "MAJOR"} for x in issues):
        verdict = "NEEDS_WORK"
        changed = True
    report = ReviewReport(
        chapter=obj["chapter"], verdict=verdict, summary=obj["summary"], issues=issues,
        strengths=tuple(obj["strengths"]), task_fulfillment=obj["task_fulfillment"],
        continuity_assessment=obj["continuity_assessment"],
        style_assessment=obj["style_assessment"], logic_assessment=obj["logic_assessment"],
        confidence=obj["confidence"], source=obj["source"],
    )
    # Enforce the persisted canonical size after normalization as well.
    if len(canonical_report_json(report)) > MAX_REPORT_CHARS:
        raise ReviewReportError("REVIEW_REPORT_TOO_LARGE")
    return ParsedReviewReport(report, changed)


def canonical_report_json(report: ReviewReport) -> str:
    if not isinstance(report, ReviewReport):
        raise ReviewReportError("report 必须是 ReviewReport")
    return json.dumps(report.to_dict(), ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False)


def report_hash(report: ReviewReport) -> str:
    return hashlib.sha256(canonical_report_json(report).encode("utf-8")).hexdigest()
