"""M6 static/privacy/adversarial policy tests (no live model semantics)."""
from __future__ import annotations

import ast
import dataclasses
import json
from pathlib import Path

import pytest

from agents.review_report import parse_review_report
from core.review_workflow import _merge_report
from core.review_preflight import PreflightIssue


ROOT = Path(__file__).resolve().parents[1]
REVIEWER_PRODUCTION = (
    ROOT / "agents/reviewer.py",
    ROOT / "core/review_workflow.py",
    ROOT / "core/review_preflight.py",
    ROOT / "core/review_context.py",
)


def _source(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _imports(path: Path) -> set[str]:
    modules: set[str] = set()
    for node in ast.walk(ast.parse(_source(path))):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            modules.add(node.module or "")
    return modules


def _called_names(path: Path) -> set[str]:
    called: set[str] = set()
    for node in ast.walk(ast.parse(_source(path))):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Name):
            called.add(node.func.id)
        elif isinstance(node.func, ast.Attribute):
            called.add(node.func.attr)
    return called


def test_reviewer_has_no_transport_or_write_tool_dependency():
    for path in REVIEWER_PRODUCTION:
        imports = _imports(path)
        source = _source(path)
        assert "httpx" not in imports
        assert "OpenAICompatibleProvider" not in source
        assert not any(name.endswith("write_tools") or name == "tools.write_tools" for name in imports)
    runner = _source(ROOT / "agents/reviewer.py")
    assert "tools=None" in runner


def test_reviewer_never_calls_writer_draft_service_confirm_or_m7_loop():
    forbidden_calls = {
        "confirm_draft", "AIChapterDraftService", "WriterRunner", "WriteWorkflow",
        "write_draft", "update_draft",
    }
    for path in REVIEWER_PRODUCTION:
        assert not (_called_names(path) & forbidden_calls), path
        source = _source(path)
        assert "max_review_rounds" not in source
        assert "while " not in source  # M6 has one review plus one context retry, not Writer↔Reviewer.


def test_architecture_b_has_no_pending_artifact_and_run_metadata_is_redacted():
    import core.review as review

    assert not (set(review.ReviewRun.__dataclass_fields__) & {
        "api_key", "authorization", "prompt", "context", "draft_body", "report",
    })
    source = _source(ROOT / "core/review.py")
    assert ".pending.json" not in source
    assert "NO_PENDING_REVIEW" in source


def test_usage_and_artifact_contract_exclude_secrets_prompt_and_full_context():
    from core.review import _ARTIFACT_FIELDS
    from llm.types import Usage

    forbidden = {"api_key", "authorization", "secret", "prompt", "context", "draft_body"}
    assert not (set(Usage.__dataclass_fields__) & forbidden)
    assert not (_ARTIFACT_FIELDS & forbidden)
    # context_hash is provenance; raw context is deliberately absent.
    assert "context_hash" in _ARTIFACT_FIELDS and "context" not in _ARTIFACT_FIELDS


def _report(issue: dict | None = None):
    issues = [] if issue is None else [{
        "id": "fixed-1", "category": issue["category"], "severity": issue["severity"],
        "title": issue["title"], "description": "deterministic fixture",
        "location": {"line_start": 1, "line_end": 1, "anchor": None},
        "evidence": issue["evidence"], "suggestion": "revise or verify against source",
    }]
    payload = {
        "chapter": 2, "verdict": "PASS", "summary": "fixture", "issues": issues,
        "strengths": ["controlled fixture"], "task_fulfillment": "checked",
        "continuity_assessment": "checked", "style_assessment": "checked",
        "logic_assessment": "checked", "confidence": 0.8, "source": "fixed-fixture",
    }
    return parse_review_report(json.dumps(payload, ensure_ascii=False), draft_line_count=5).report


@pytest.mark.parametrize("fixture", [
    {"category": "CHARACTER", "severity": "MAJOR", "title": "身份与正式人物卡冲突",
     "evidence": "人物卡固定为沈砚，正文却称作他人"},
    {"category": "WORLD", "severity": "BLOCKER", "title": "正式世界规则被违反",
     "evidence": "契约规则明确禁止该能力"},
    {"category": "TIMELINE", "severity": "MAJOR", "title": "时间顺序不可能",
     "evidence": "事件发生在角色到场之前"},
])
def test_fixed_structured_contradictions_fail_closed(fixture):
    # These are structured expected findings, not claims that a fake model detected them.
    report = _report(fixture)
    assert report.verdict == "NEEDS_WORK"
    assert report.issues[0].category == fixture["category"]


def test_fixed_foreshadowing_control_does_not_force_needs_work():
    report = _report({
        "category": "FORESHADOWING", "severity": "INFO", "title": "伏笔暂未解释",
        "evidence": "本章只埋下线索，尚无正式事实冲突",
    })
    assert report.verdict == "PASS"


def test_deterministic_preflight_blocker_cannot_be_erased_by_model_pass():
    merged = _merge_report(
        _report(),
        (PreflightIssue("BLOCKER", "FORMAL_SOURCE_CONTRADICTION",
                        "fixed contradiction", "characters/shen-yan.md"),),
        chapter=2, draft_line_count=5,
    )
    assert merged.verdict == "NEEDS_WORK"
    assert any(issue.id == "FORMAL_SOURCE_CONTRADICTION" for issue in merged.issues)
