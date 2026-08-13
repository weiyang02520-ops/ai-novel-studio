"""M7 localhost HTTP E2E through the production subprocess compose command."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from core.chapter import draft_path, parse_frontmatter
from core.project import create_project, open_project
from core.storage import ProjectStore, atomic_write_text


ROOT = Path(__file__).resolve().parents[1]


def _task(source="structured"):
    return {"chapter": 1, "goal": "进入停生院", "target_chars": 1000, "title": "契纹",
            "opening": "黄昏", "conflict": "无名尸", "turning_point": "发现契纹",
            "ending_hook": "门后脚步", "characters": ["沈砚"], "world_elements": ["契纹"],
            "continuity_requirements": [], "style_requirements": ["克制"],
            "forbidden_changes": [], "user_instruction": "", "chief_brief": "推进调查",
            "source": source}


def _report(verdict="PASS", title="因果缺口"):
    issues = [] if verdict == "PASS" else [{
        "id": "logic-1", "category": "LOGIC", "severity": "MAJOR", "title": title,
        "description": "行动缺少动机。", "location": {"line_start": 1, "line_end": 1, "anchor": "沈砚"},
        "evidence": "沈砚推门。", "suggestion": "补充线索。",
    }]
    return {"chapter": 1, "verdict": verdict, "summary": "审查完成。", "issues": issues,
            "strengths": ["视角稳定"], "task_fulfillment": "完成目标。",
            "continuity_assessment": "无冲突。", "style_assessment": "稳定。",
            "logic_assessment": "可追踪。", "confidence": .9, "source": "reviewer"}


def _http(content, model="local"):
    return {"id": "m7", "model": model, "choices": [{"index": 0,
            "message": {"role": "assistant", "content": content}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}}


def _setup(server, tmp_path):
    data = tmp_path / "novels"
    project = create_project(ProjectStore(data), "Compose E2E", project_id="m7-e2e")
    atomic_write_text(project.dir / "rules/writing_rules.md", "# 规则\n保持克制。")
    atomic_write_text(project.dir / "outline/summary.md", "# 总纲\n契纹谜案。")
    atomic_write_text(project.dir / "outline/volumes/vol001.md", "# 第一卷\n调查。")
    atomic_write_text(project.dir / "outline/chapters/ch0001.md", "# 第一章\n进入停生院。")
    atomic_write_text(project.dir / "characters/shen.md", "# 沈砚\n验尸人。")
    atomic_write_text(project.dir / "world/mark.md", "# 契纹\n黄昏出现。")
    config = tmp_path / "settings.json"
    config.write_text(json.dumps({
        "default_model": {"provider": "openai_compatible", "base_url": server.base_url,
            "model": "local", "temperature": .1, "capabilities": {"tool_calls": False,
            "vision": False, "max_context_tokens": 16000}, "secret_reference": ""},
        "models": {}, "workflow": {"max_review_rounds": 3},
        "context": {"reserve_output_tokens": 1000, "review_reserve_output_tokens": 600,
                    "max_recent_chapters": 5, "max_recent_text_chars": 3000},
    }, ensure_ascii=False), encoding="utf-8")
    return project, data, config


def _run(project, data, config, *extra, stream=False):
    tail = ["compose", project.id, "1", "--target-chars", "1000", *extra]
    if not stream:
        tail.append("--no-stream")
    return subprocess.run([sys.executable, "-m", "adapters.cli", "--config", str(config),
        "--data-dir", str(data), "--usage-path", str(config.parent / "usage.jsonl"), *tail],
        cwd=ROOT, capture_output=True, text=True, timeout=45)


def _queue(server, *contents):
    server.responses.extend((200, _http(content if isinstance(content, str)
        else json.dumps(content, ensure_ascii=False))) for content in contents)


def test_compose_http_first_pass_then_explicit_confirm(server, tmp_path):
    project, data, config = _setup(server, tmp_path)
    _queue(server, _task(), "Draft A。", _report())
    composed = _run(project, data, config)
    assert composed.returncode == 0, composed.stdout + composed.stderr
    meta, body = parse_frontmatter(draft_path(project, 1).read_text(encoding="utf-8"))
    assert meta["status"] == "ready" and body == "Draft A。"
    assert open_project(project.store, project.id).current_chapter == 0
    confirmed = subprocess.run([sys.executable, "-m", "adapters.cli", "--config", str(config),
        "--data-dir", str(data), "chapter", "confirm", project.id, "1"], cwd=ROOT,
        capture_output=True, text=True, timeout=30)
    assert confirmed.returncode == 0, confirmed.stdout + confirmed.stderr
    assert (project.dir / "chapters/ch0001.md").is_file()


def test_compose_http_fail_replan_rewrite_then_pass(server, tmp_path):
    project, data, config = _setup(server, tmp_path)
    _queue(server, _task(), "Draft A。", _report("NEEDS_WORK"), _task(), "Draft B。", _report())
    result = _run(project, data, config, "--show-rounds")
    assert result.returncode == 0, result.stdout + result.stderr
    assert parse_frontmatter(draft_path(project, 1).read_text(encoding="utf-8"))[1] == "Draft B。"
    assert len(server.requests) == 6
    assert [request["body_json"]["model"] for request in server.requests] == ["local"] * 6


def test_compose_http_max_rounds_is_exactly_bounded(server, tmp_path):
    project, data, config = _setup(server, tmp_path)
    _queue(server, _task(), "A。", _report("NEEDS_WORK", "one"), _task(), "B。",
           _report("NEEDS_WORK", "two"), _task(), "C。", _report("NEEDS_WORK", "three"))
    result = _run(project, data, config, "--max-rounds", "3")
    assert result.returncode == 2 and "MAX_REVIEW_ROUNDS" in result.stdout
    assert len(server.requests) == 9  # 3 reviews, initial writer, and 2 rewrites with Chief plans


def test_compose_http_stall_stops_before_second_rewrite(server, tmp_path):
    project, data, config = _setup(server, tmp_path)
    same = _report("NEEDS_WORK", "same")
    _queue(server, _task(), "A。", same, _task(), "B。", same)
    result = _run(project, data, config)
    assert result.returncode == 2 and "STALLED_REVIEW" in result.stdout
    assert len(server.requests) == 6
    assert parse_frontmatter(draft_path(project, 1).read_text(encoding="utf-8"))[1] == "B。"


def test_compose_http_writer_interrupt_then_new_process_resume(server, tmp_path):
    project, data, config = _setup(server, tmp_path)
    _queue(server, _task())
    server.stream_mode = "interrupt"
    server.stream_chunks = [json.dumps({"choices": [{"delta": {"content": "Partial。"}}]}, ensure_ascii=False)]
    interrupted = _run(project, data, config, stream=True)
    assert interrupted.returncode == 130, interrupted.stdout + interrupted.stderr
    assert not draft_path(project, 1).exists()
    server.stream_mode = None
    sse = "data: " + json.dumps({"choices": [{"delta": {"content": "Rest。"}}]}, ensure_ascii=False)
    sse += "\n\ndata: " + json.dumps({"choices": [{"delta": {}, "finish_reason": "stop"}]})
    sse += "\n\ndata: [DONE]\n\n"
    server.responses += [(200, sse), (200, _http(json.dumps(_report(), ensure_ascii=False)))]
    resumed = _run(project, data, config, "--resume", stream=True)
    assert resumed.returncode == 0, resumed.stdout + resumed.stderr
    assert "Partial。" in parse_frontmatter(draft_path(project, 1).read_text(encoding="utf-8"))[1]


def test_compose_http_review_stale_preserves_external_bytes(server, tmp_path):
    project, data, config = _setup(server, tmp_path)
    path = draft_path(project, 1); external = b"\nEXTERNAL_REVIEW"
    def race(_request, count):
        if count == 3:
            path.write_bytes(path.read_bytes() + external)
    server.before_response = race
    _queue(server, _task(), "A。", _report())
    result = _run(project, data, config)
    assert result.returncode == 2 and "STALE_REVIEW_DRAFT" in result.stdout
    assert path.read_bytes().endswith(external)


def test_compose_http_rewrite_stale_preserves_external_bytes(server, tmp_path):
    project, data, config = _setup(server, tmp_path)
    path = draft_path(project, 1); external = b"\nEXTERNAL_REWRITE"
    def race(_request, count):
        if count == 5:
            path.write_bytes(path.read_bytes() + external)
    server.before_response = race
    _queue(server, _task(), "A。", _report("NEEDS_WORK"), _task(), "MODEL B。")
    result = _run(project, data, config)
    assert result.returncode == 2 and "STALE_DRAFT_REVISION" in result.stdout
    assert path.read_bytes().endswith(external)
