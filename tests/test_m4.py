from __future__ import annotations

import json

import pytest

from agents.definitions import m4_chief_agent_def
from agents.types import AgentContext
from core.config import ModelConfig, Settings
from core.context import collect_project_context, render_context_items
from core.history import list_history, undo_last
from core.knowledge import doctor, inspect_knowledge_status, search_knowledge
from core.mutation import ABSENT, MutationError, MutationRequest, MutationService, file_revision
from core.project import create_project
from core.storage import ProjectStore, atomic_write_text
from llm.testing import FakeProvider
from llm.types import ChatResult, ToolCall
from agents.runtime import AgentSession
from tools.read_tools import build_chief_registry
from test_m3_cli import _create_project, _run, _write_settings


@pytest.fixture
def project(tmp_path):
    return create_project(ProjectStore(tmp_path / "novels"), "M4 小说", "m4-novel")


def req(rel, text, expected=ABSENT, kind="outline"):
    return MutationRequest("ai.test", rel, text, expected, kind, "chief")


def test_mutation_create_update_undo(project):
    svc = MutationService(project)
    created = svc.mutate(req("outline/volumes/vol001.md", "# 第一卷\n"))
    assert created.created and created.history_seq == 1
    path = project.dir / "outline/volumes/vol001.md"
    updated = svc.mutate(req("outline/volumes/vol001.md", "# 第一卷\n\n新目标\n", file_revision(path)))
    assert updated.diff.added_lines == 2 and len(list_history(project)) == 2
    undo_last(project)
    assert path.read_text(encoding="utf-8") == "# 第一卷\n"


def test_stale_noop_empty_nul_and_limit(project):
    path = project.dir / "outline/summary.md"
    svc = MutationService(project)
    with pytest.raises(MutationError, match="STALE_REVISION"):
        svc.mutate(req("outline/summary.md", "x", "bad"))
    old = path.read_text(encoding="utf-8")
    with pytest.raises(MutationError, match="NO_CHANGE"):
        svc.mutate(req("outline/summary.md", old, file_revision(path)))
    assert not list_history(project)
    with pytest.raises(MutationError, match="EMPTY_DESTRUCTIVE_WRITE"):
        svc.preview(req("outline/summary.md", "  ", file_revision(path)))
    with pytest.raises(MutationError, match="NUL_REJECTED"):
        svc.preview(req("outline/summary.md", "a\0b", file_revision(path)))
    with pytest.raises(MutationError, match="CONTENT_TOO_LARGE"):
        svc.preview(req("characters/x.md", "x" * 300001, ABSENT, "character"))


def test_write_failure_restores_and_has_no_history(project):
    path = project.dir / "outline/summary.md"; old = path.read_bytes()
    def fail(_path, _text): raise OSError("boom")
    with pytest.raises(MutationError, match="已恢复原字节"):
        MutationService(project, writer=fail).mutate(req("outline/summary.md", "# changed", file_revision(path)))
    assert path.read_bytes() == old and not list_history(project)


def test_post_write_verify_restores(project):
    path = project.dir / "outline/summary.md"; old = path.read_bytes()
    def corrupt(target, _text): atomic_write_text(target, "corrupt")
    with pytest.raises(MutationError, match="POST_WRITE_VERIFY_FAILED"):
        MutationService(project, writer=corrupt).mutate(req("outline/summary.md", "wanted", file_revision(path)))
    assert path.read_bytes() == old


def test_history_commit_failure_restores(project):
    path = project.dir / "outline/summary.md"; old = path.read_bytes()
    from core.history import prepare_snapshot
    def factory(*args):
        snap = prepare_snapshot(*args)
        snap.commit = lambda: (_ for _ in ()).throw(OSError("commit"))
        return snap
    with pytest.raises(MutationError, match="已恢复原字节"):
        MutationService(project, snapshot_factory=factory).mutate(req("outline/summary.md", "# changed", file_revision(path)))
    assert path.read_bytes() == old and not list_history(project)


def test_rollback_failure_is_high_severity(project):
    path = project.dir / "outline/summary.md"
    from core.history import prepare_snapshot
    def factory(*args):
        snap = prepare_snapshot(*args)
        snap.commit = lambda: (_ for _ in ()).throw(OSError("commit"))
        snap.restore = lambda: (_ for _ in ()).throw(OSError("rollback"))
        return snap
    with pytest.raises(MutationError, match="ROLLBACK_FAILED"):
        MutationService(project, snapshot_factory=factory).mutate(req("outline/summary.md", "# changed", file_revision(path)))


def _ctx(project):
    cfg = ModelConfig(base_url="http://127.0.0.1:9", model="m", tool_calls=True)
    return AgentContext(project, Settings.load(project.dir / "missing.json"), FakeProvider(cfg),
                        build_chief_registry(), m4_chief_agent_def())


def call(ctx, name, args): return ctx.tool_registry.execute(ctx.agent_def, name, json.dumps(args), ctx)


def test_outline_tool_and_revision_readback(project):
    ctx = _ctx(project)
    out, trace = call(ctx, "update_outline", {"scope":"volume", "number":2,
        "text":"# 第二卷\n", "expected_revision":ABSENT})
    assert trace.success and trace.mutates_project and "WRITE_OK" in out
    read, _ = call(ctx, "read_outline", {"volume":2})
    assert "REVISION_SHA256" in read and "# 第二卷" in read


def test_chinese_character_stable_slug_h1_and_undo(project):
    ctx = _ctx(project)
    args = {"name":"林小满", "text":"性格坚定。", "expected_revision":ABSENT}
    out, trace = call(ctx, "update_character", args)
    assert trace.success
    files = list((project.dir / "characters").glob("char-*.md"))
    assert len(files) == 1 and files[0].read_text(encoding="utf-8").startswith("# 林小满")
    undo_last(project); assert not files[0].exists()


def test_world_and_memory_transactions(project):
    ctx = _ctx(project)
    out, tr = call(ctx, "update_world", {"name":"北门", "text":"钥匙规则", "expected_revision":ABSENT})
    assert tr.success
    mem = project.dir / "memory/long_term.md"
    out, tr = call(ctx, "save_memory_entry", {"kind":"long_term", "text":"记住北门",
                                               "expected_revision":file_revision(mem)})
    assert tr.success and "## 20" in mem.read_text(encoding="utf-8")
    undo_last(project); assert "记住北门" not in mem.read_text(encoding="utf-8")


def test_runtime_rejects_multi_mutation_batch(project):
    ctx = _ctx(project)
    calls = [ToolCall("a", "update_outline", json.dumps({"scope":"volume","number":1,"text":"# A","expected_revision":ABSENT})),
             ToolCall("b", "update_world", json.dumps({"name":"B","text":"# B","expected_revision":ABSENT}))]
    ctx.provider.replies = [ChatResult(text="", tool_calls=calls), ChatResult(text="批次已拒绝")]
    result = AgentSession(ctx).ask("同时修改")
    assert result.status == "completed" and result.tool_calls_count == 0
    assert all(t.error.startswith("MUTATION_BATCH_REJECTED") for t in result.tool_trace)
    assert not (project.dir / "outline/volumes/vol001.md").exists()


def test_knowledge_search_status_doctor_and_context(project):
    atomic_write_text(project.dir / "characters/a.md", "# 阿甲\n北门")
    atomic_write_text(project.dir / "world/north.md", "# 北门\n规则")
    hits = search_knowledge(project, "北门")
    assert {h["type"] for h in hits} == {"FACT_SOURCE"}
    assert inspect_knowledge_status(project)["characters_count"] == 1
    assert doctor(project) == []
    items = collect_project_context(project, character_names=["阿甲"], include_memory=True)
    assert any(i.type == "CHARACTER" for i in items)
    pack = render_context_items(items, 800)
    assert len(pack) <= 800 and "[PROJECT_DATA_BEGIN]" in pack and "chapters/" not in pack


def test_doctor_duplicate_h1(project):
    atomic_write_text(project.dir / "characters/a.md", "# 同名\n")
    atomic_write_text(project.dir / "characters/b.md", "# 同名\n")
    assert any(i["code"] == "DUPLICATE_CHARACTER_H1" for i in doctor(project))


def _tool_body(call_id, name, arguments):
    return {"choices":[{"index":0,"message":{"role":"assistant","content":"","tool_calls":[{
        "id":call_id,"type":"function","function":{"name":name,"arguments":json.dumps(arguments, ensure_ascii=False)}}]},
        "finish_reason":"tool_calls"}],"model":"mock-model"}


def test_local_http_real_cli_mutation_loop_and_undo(tmp_path, server):
    pid = _create_project(tmp_path, "m4-http")
    _write_settings(tmp_path, base_url=server.base_url)
    summary = tmp_path / "novels" / pid / "outline" / "summary.md"
    rev = file_revision(summary)
    server.responses.extend([
        (200, _tool_body("read-1", "read_outline", {})),
        (200, _tool_body("write-1", "update_outline", {"scope":"summary", "text":"# 新总纲\n\n找到北门钥匙。\n",
                                                            "expected_revision":rev})),
        (200, _tool_body("read-2", "read_outline", {})),
        (200, {"choices":[{"message":{"role":"assistant","content":"已更新并复核总纲。"},"finish_reason":"stop"}],
               "model":"mock-model"}),
    ])
    result = _run(tmp_path, "chat", "把总纲改成找到北门钥匙。", "--project", pid, "--show-tools", "--show-diff")
    assert result.returncode == 0, result.stdout + result.stderr
    assert summary.read_text(encoding="utf-8").startswith("# 新总纲")
    assert "[tool] update_outline WRITE OK" in result.stdout and "--- before" in result.stdout
    history = tmp_path / "novels" / pid / ".history" / "index.jsonl"
    assert "ai.update_outline" in history.read_text(encoding="utf-8")
    undone = _run(tmp_path, "undo-last-change", pid)
    assert undone.returncode == 0 and "UNDO OK" in undone.stdout
    assert file_revision(summary) == rev


def test_weak_model_cli_blocks_mutation(tmp_path, server):
    pid = _create_project(tmp_path, "m4-weak")
    _write_settings(tmp_path, base_url=server.base_url, tool_calls=False)
    summary = tmp_path / "novels" / pid / "outline" / "summary.md"
    before = summary.read_bytes()
    result = _run(tmp_path, "chat", "请修改总纲", "--project", pid)
    assert result.returncode == 1
    assert "mutation 已安全阻止" in result.stdout
    assert summary.read_bytes() == before and server.request_count() == 0
