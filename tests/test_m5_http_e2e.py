"""M5 localhost HTTP E2E through the real subprocess CLI and provider stack."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from core.ai_draft import AIChapterDraftService, AIDraftError
from core.chapter import confirm_draft, draft_path, parse_frontmatter, write_draft
from core.history import list_history, undo_last
from core.mutation import ABSENT, file_revision
from core.project import create_project
from core.storage import ProjectStore, atomic_write_text
from core.write_workflow import WriteRequest, WriteWorkflow, WriteWorkflowError
from core.config import ModelConfig
from llm.openai_compatible import OpenAICompatibleProvider


ROOT = Path(__file__).resolve().parents[1]


def test_m5_subprocess_chief_then_writer_sse(server, tmp_path):
    data_dir = tmp_path / "novels"
    project = create_project(ProjectStore(data_dir), "E2E", project_id="m5-e2e")
    atomic_write_text(project.dir / "rules/writing_rules.md", "# 规则\n保持克制。")
    atomic_write_text(project.dir / "outline/summary.md", "# 总纲\n鹤梁山谜案。")
    atomic_write_text(project.dir / "outline/volumes/vol001.md", "# 第一卷\n调查契约。")
    atomic_write_text(project.dir / "outline/chapters/ch0002.md", "# 第二章\n沈砚进入停生院。")
    atomic_write_text(project.dir / "characters/shen-yan.md", "# 沈砚\n验尸人。")
    atomic_write_text(project.dir / "world/contract.md", "# 契纹\n死者印记。")
    write_draft(project, 1, "第一章", "前夜山雨不停。")
    confirm_draft(project, 1)

    task = {
        "chapter": 2, "goal": "进入停生院", "target_chars": 1000, "title": "无名尸",
        "opening": "山雨刚停", "conflict": "尸体无名", "turning_point": "发现契纹",
        "ending_hook": "门后有脚步", "characters": ["沈砚"], "world_elements": ["契纹"],
        "continuity_requirements": [], "style_requirements": ["克制"],
        "forbidden_changes": [], "user_instruction": "", "chief_brief": "推进调查",
        "source": "structured",
    }
    server.responses.append((200, {
        "model": "chief-local", "choices": [{"message": {"role": "assistant",
        "content": json.dumps(task, ensure_ascii=False)}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30},
    }))
    server.responses.append((200, {
        "model": "writer-local", "choices": [{"message": {"role": "assistant",
        "content": "山雨刚停。沈砚站在停生院门前。第七具尸体仍没有名字。"},
        "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 50, "completion_tokens": 20, "total_tokens": 70},
    }))
    config = tmp_path / "settings.json"
    config.write_text(json.dumps({
        "default_model": {"provider": "openai_compatible", "base_url": server.base_url,
                          "model": "local", "temperature": 0.2,
                          "capabilities": {"tool_calls": False, "vision": False,
                                           "max_context_tokens": 16000},
                          "secret_reference": ""},
        "models": {}, "context": {"reserve_output_tokens": 1000,
                                      "max_recent_chapters": 5,
                                      "max_recent_text_chars": 3000},
    }, ensure_ascii=False), encoding="utf-8")
    usage = tmp_path / "usage.jsonl"
    result = subprocess.run(
        [sys.executable, "-m", "adapters.cli", "--config", str(config),
         "--data-dir", str(data_dir), "--usage-path", str(usage),
         "write", project.id, "2", "--target-chars", "1000", "--no-stream"],
        cwd=ROOT, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "DRAFT SAVED" in result.stdout
    metadata, body = parse_frontmatter(draft_path(project, 2).read_text(encoding="utf-8"))
    assert metadata["origin"] == "ai" and metadata["status"] == "draft"
    assert metadata["generation_state"] == "complete" and metadata["generation_mode"] == "new"
    assert body == "山雨刚停。沈砚站在停生院门前。第七具尸体仍没有名字。"
    assert project.current_chapter == 1
    assert (project.dir / "chapters/ch0001.md").is_file()
    assert list_history(project)[0]["operation"] == "ai.draft.create"
    assert len(server.requests) == 2
    writer_request = server.requests[1]["body_json"]
    assert writer_request.get("stream") is not True
    prompt = writer_request["messages"][1]["content"]
    for expected in ("TASK_CARD", "rules/writing_rules.md", "outline/chapters/ch0002.md",
                     "characters/shen-yan.md", "world/contract.md", "chapters/ch0001.md"):
        assert expected in prompt


def test_m5_local_http_modes_matrix(server, tmp_path):
    project = create_project(ProjectStore(tmp_path / "novels"), "Modes", project_id="m5-modes")
    atomic_write_text(project.dir / "outline/chapters/ch0001.md", "# 第一章\n测试。")
    cfg = ModelConfig(provider="openai_compatible", base_url=server.base_url, model="local",
                      max_context_tokens=16000)
    chief = OpenAICompatibleProvider(cfg)
    writer = OpenAICompatibleProvider(cfg)
    flow = WriteWorkflow(chief_provider=chief, writer_provider=writer,
                         chief_prompt="json only", writer_prompt="prose only",
                         settings=SimpleNamespace(context={"reserve_output_tokens": 1000,
                            "max_recent_chapters": 5, "max_recent_text_chars": 3000}))
    task = {"chapter": 1, "goal": "测试", "target_chars": 1000, "title": "",
            "characters": [], "world_elements": [], "continuity_requirements": [],
            "style_requirements": [], "forbidden_changes": [], "source": "structured"}

    def chief_response():
        server.responses.append((200, {"model": "chief", "choices": [{"message": {
            "role": "assistant", "content": json.dumps(task, ensure_ascii=False)},
            "finish_reason": "stop"}]}))

    def stream(text, finish="stop", mode="full"):
        server.stream_mode = mode
        server.stream_chunks = [json.dumps({"choices": [{"delta": {"content": text}}]}, ensure_ascii=False)]
        if mode == "full":
            server.stream_chunks += [json.dumps({"choices": [{"delta": {}, "finish_reason": finish}]}), "[DONE]"]

    # LENGTH saves a truncated canonical draft.
    chief_response(); stream("截断正文。", "length")
    result = flow.run(WriteRequest(project, chapter=1, target_chars=1000))
    assert result.draft_result.state == "truncated"
    undo_last(project)

    # REWRITE and CONTINUE both round-trip through HTTP and undo.
    created = AIChapterDraftService(project).finalize(
        chapter=1, title="", body="A。", mode="new", generation_state="complete",
        model="m", context_hash="c", task_hash="t", expected_revision=ABSENT)
    chief_response(); stream("B。")
    flow.run(WriteRequest(project, chapter=1, mode="rewrite", target_chars=1000))
    assert parse_frontmatter(draft_path(project, 1).read_text(encoding="utf-8"))[1] == "B。"
    undo_last(project)
    assert file_revision(draft_path(project, 1)) == created.revision
    chief_response(); stream("C。")
    flow.run(WriteRequest(project, chapter=1, mode="continue", target_chars=1000))
    assert "A。\nC。" in parse_frontmatter(draft_path(project, 1).read_text(encoding="utf-8"))[1]
    undo_last(project)

    # Disconnect preserves partial; a new HTTP stream resumes it cross-run.
    chief_response(); stream("P。", mode="interrupt")
    interrupted = flow.run(WriteRequest(project, chapter=1, mode="continue", target_chars=1000))
    assert interrupted.status == "interrupted"
    stream("R。")
    flow.run(WriteRequest(project, chapter=1, mode="resume"))
    assert "A。\nP。\nR。" in parse_frontmatter(draft_path(project, 1).read_text(encoding="utf-8"))[1]
    undo_last(project)

    # External bytes written from the streaming callback win the stale race.
    chief_response(); stream("MODEL。")
    target = draft_path(project, 1)
    external = target.read_bytes() + b"EXTERNAL"
    with pytest.raises(AIDraftError, match="STALE_DRAFT_REVISION"):
        flow.run(WriteRequest(project, chapter=1, mode="rewrite", target_chars=1000),
                 on_text_delta=lambda _: target.write_bytes(external))
    assert target.read_bytes() == external
    from core.generation import GenerationWorkspace
    GenerationWorkspace(project, 1).cleanup()

    # Manual protection rejects before either HTTP provider receives a request.
    manual = create_project(project.store, "Manual", project_id="m5-manual")
    atomic_write_text(manual.dir / "outline/chapters/ch0001.md", "# 第一章\n测试。")
    write_draft(manual, 1, "", "manual")
    before = server.request_count()
    with pytest.raises(WriteWorkflowError, match="MANUAL_DRAFT_PROTECTED"):
        flow.run(WriteRequest(manual, chapter=1, mode="rewrite", target_chars=1000))
    assert server.request_count() == before
    chief.close(); writer.close()
