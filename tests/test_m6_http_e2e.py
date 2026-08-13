"""M6 localhost HTTP E2E through subprocess CLI and production review stack."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from core.ai_draft import AIChapterDraftService
from core.chapter import draft_path, parse_frontmatter
from core.history import list_history
from core.mutation import ABSENT
from core.project import create_project
from core.storage import ProjectStore, atomic_write_text

ROOT = Path(__file__).resolve().parents[1]


def _report(verdict="PASS", *, severity=None):
    issues = []
    if severity:
        issues.append({
            "id": "logic-1", "category": "LOGIC", "severity": severity,
            "title": "因果缺口", "description": "行动缺少动机。",
            "location": {"line_start": 1, "line_end": 1, "anchor": "沈砚"},
            "evidence": "沈砚推门而入。", "suggestion": "补充触发行动的线索。",
        })
    return {
        "chapter": 1, "verdict": verdict, "summary": "审查完成。", "issues": issues,
        "strengths": ["视角稳定"], "task_fulfillment": "完成章纲目标。",
        "continuity_assessment": "未见冲突。", "style_assessment": "风格稳定。",
        "logic_assessment": "因果可追踪。", "confidence": 0.9, "source": "reviewer",
    }


def _http_result(content, *, model="reviewer-local"):
    return {
        "id": "m6", "model": model,
        "choices": [{"index": 0, "message": {"role": "assistant", "content": content},
                     "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 30, "completion_tokens": 20, "total_tokens": 50},
    }


def _setup(server, tmp_path, *, huge=False):
    data_dir = tmp_path / "novels"
    project = create_project(ProjectStore(data_dir), "Reviewer E2E", project_id="m6-e2e")
    atomic_write_text(project.dir / "rules/writing_rules.md", "# 规则\n使用限知视角。")
    atomic_write_text(project.dir / "outline/summary.md", "# 总纲\n调查契纹谜案。")
    atomic_write_text(project.dir / "outline/volumes/vol001.md", "# 第一卷\n进入停生院。")
    atomic_write_text(project.dir / "outline/chapters/ch0001.md", "# 第一章\n沈砚发现契纹。")
    atomic_write_text(project.dir / "characters/shen-yan.md", "# 沈砚\n谨慎的验尸人。")
    atomic_write_text(project.dir / "world/contract.md", "# 契纹\n只在黄昏出现。")
    body = "沈砚推门而入。\n黄昏的契纹浮在尸体腕间。\n"
    if huge:
        body = "HEAD\n" + "细节。" * 8_000 + "\nMIDDLE\n" + "线索。" * 8_000 + "\nTAIL"
    AIChapterDraftService(project).finalize(
        chapter=1, title="契纹", body=body, mode="new", generation_state="complete",
        model="writer", context_hash="c" * 64, task_hash="t" * 64,
        expected_revision=ABSENT,
    )
    config = tmp_path / "settings.json"
    config.write_text(json.dumps({
        "default_model": {"provider": "openai_compatible", "base_url": server.base_url,
                          "model": "review-default", "temperature": 0.1,
                          "capabilities": {"tool_calls": False, "vision": False,
                                           "max_context_tokens": 16_000},
                          "secret_reference": ""},
        "models": {"reviewer": {"provider": "openai_compatible", "base_url": server.base_url,
                                   "model": "review-role", "temperature": 0.1,
                                   "capabilities": {"tool_calls": False, "vision": False,
                                                    "max_context_tokens": 16_000},
                                   "secret_reference": ""}},
        "context": {"review_reserve_output_tokens": 600, "max_recent_chapters": 5,
                    "max_recent_text_chars": 3000},
    }, ensure_ascii=False), encoding="utf-8")
    return project, data_dir, config


def _run(project, data_dir, config, *tail):
    return subprocess.run(
        [sys.executable, "-m", "adapters.cli", "--config", str(config),
         "--data-dir", str(data_dir), "--usage-path", str(config.parent / "usage.jsonl"),
         *tail], cwd=ROOT, capture_output=True, text=True, timeout=30,
    )


def _review(project, data_dir, config):
    return _run(project, data_dir, config, "review", project.id, "1")


def test_http_pass_then_explicit_confirm(server, tmp_path):
    project, data, config = _setup(server, tmp_path)
    server.responses.append((200, _http_result(json.dumps(_report(), ensure_ascii=False))))
    reviewed = _review(project, data, config)
    assert reviewed.returncode == 0, reviewed.stdout + reviewed.stderr
    meta, body = parse_frontmatter(draft_path(project, 1).read_text(encoding="utf-8"))
    assert meta["status"] == "ready" and "契纹" in body
    assert (project.dir / "review/ch0001.review.json").is_file()
    confirmed = _run(project, data, config, "chapter", "confirm", project.id, "1")
    assert confirmed.returncode == 0, confirmed.stdout + confirmed.stderr
    assert (project.dir / "chapters/ch0001.md").is_file()
    assert [x["operation"] for x in list_history(project)][:2] == ["chapter.confirm", "ai.review.pass"]


def test_http_needs_work_stays_draft(server, tmp_path):
    project, data, config = _setup(server, tmp_path)
    server.responses.append((200, _http_result(json.dumps(
        _report("NEEDS_WORK", severity="MAJOR"), ensure_ascii=False))))
    result = _review(project, data, config)
    assert result.returncode == 0, result.stdout + result.stderr
    assert parse_frontmatter(draft_path(project, 1).read_text(encoding="utf-8"))[0]["status"] == "draft"
    assert json.loads((project.dir / "review/ch0001.review.json").read_text(encoding="utf-8"))["verdict"] == "NEEDS_WORK"


def test_http_malformed_then_valid_repair(server, tmp_path):
    project, data, config = _setup(server, tmp_path)
    server.responses += [(200, _http_result("not-json")),
                         (200, _http_result(json.dumps(_report(), ensure_ascii=False)))]
    result = _review(project, data, config)
    assert result.returncode == 0, result.stdout + result.stderr
    assert server.request_count() == 2
    repair = server.requests[1]["body_json"]["messages"]
    assert "失败结果" in repair[1]["content"] and "REVIEW_DATA_BEGIN" not in repair[1]["content"]


def test_http_double_malformed_is_fail_closed(server, tmp_path):
    project, data, config = _setup(server, tmp_path)
    before = draft_path(project, 1).read_bytes()
    server.responses += [(200, _http_result("bad-one")), (200, _http_result("bad-two"))]
    result = _review(project, data, config)
    assert result.returncode != 0
    assert draft_path(project, 1).read_bytes() == before
    assert not (project.dir / "review/ch0001.review.json").exists()


def test_http_context_too_long_retries_with_smaller_request(server, tmp_path):
    project, data, config = _setup(server, tmp_path, huge=True)
    server.responses += [
        (400, {"error": {"message": "maximum context length exceeded", "code": "context_length_exceeded"}}),
        (200, _http_result(json.dumps(_report(), ensure_ascii=False))),
    ]
    result = _review(project, data, config)
    assert result.returncode == 0, result.stdout + result.stderr
    assert server.request_count() == 2
    first = server.requests[0]["body_json"]["messages"][1]["content"]
    second = server.requests[1]["body_json"]["messages"][1]["content"]
    assert len(second) < len(first)
    artifact = json.loads((project.dir / "review/ch0001.review.json").read_text(encoding="utf-8"))
    assert artifact["verdict"] == "NEEDS_WORK"


def test_http_external_edit_before_response_is_stale_and_preserved(server, tmp_path):
    project, data, config = _setup(server, tmp_path)
    path = draft_path(project, 1)
    external = b"external-response-race"
    def race(_request, count):
        if count == 1:
            path.write_bytes(path.read_bytes() + external)
    server.before_response = race
    server.responses.append((200, _http_result(json.dumps(_report(), ensure_ascii=False))))
    result = _review(project, data, config)
    assert result.returncode != 0
    assert path.read_bytes().endswith(external)
    assert not (project.dir / "review/ch0001.review.json").exists()
