from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from core.context import collect_project_context, collect_recent_chapters, render_context_items
from core.history import list_history, prepare_snapshot, undo_last
from core.knowledge import doctor, search_knowledge
from core.memory import MEMORY_KINDS, memory_target_for_kind
from core.mutation import ABSENT, MutationError, MutationRequest, MutationService, file_revision
from core.project import create_project
from core.storage import ProjectStore, atomic_write_text
from tools.read_tools import build_chief_registry
from agents.definitions import m4_chief_agent_def
from agents.types import AgentContext
from core.config import ModelConfig, Settings
from llm.testing import FakeProvider
from llm.types import ChatResult
from agents.runtime import AgentSession
from test_m3_cli import _create_project, _run, _write_settings


@pytest.fixture
def project(tmp_path):
    return create_project(ProjectStore(tmp_path / "novels"), "硬化", "hardening")


def request(rel, text, expected, kind="outline"):
    return MutationRequest("ai.hardening", rel, text, expected, kind, "chief")


def context(project):
    cfg = ModelConfig(base_url="http://127.0.0.1:9", model="m", tool_calls=True)
    return AgentContext(project, Settings.load(project.dir / "none.json"), FakeProvider(cfg),
                        build_chief_registry(), m4_chief_agent_def())


def tool(ctx, name, args):
    return ctx.tool_registry.execute(ctx.agent_def, name, json.dumps(args, ensure_ascii=False), ctx)


def test_memory_kind_contract_and_read_append_read_undo(project):
    assert MEMORY_KINDS == {"long_term", "timeline", "characters", "world", "index", "foreshadowing"}
    ctx = context(project)
    before, trace = tool(ctx, "read_memory", {"kind":"long_term"})
    assert trace.success and "TYPE: DERIVED_MEMORY" in before and "REVISION_SHA256:" in before
    rev = before.split("REVISION_SHA256: ", 1)[1].splitlines()[0]
    out, trace = tool(ctx, "save_memory_entry", {"kind":"long_term", "text":"北门钥匙", "expected_revision":rev})
    assert trace.success and "WRITE_OK" in out
    after, _ = tool(ctx, "read_memory", {"kind":"long_term"})
    assert "北门钥匙" in after and rev not in after.split("REVISION_SHA256: ", 1)[1].splitlines()[0]
    undo_last(project)
    assert "北门钥匙" not in (project.dir / memory_target_for_kind("long_term")).read_text(encoding="utf-8")


def test_read_memory_absent_and_invalid_kind(project):
    ctx = context(project)
    (project.dir / "memory/world.md").unlink()
    out, trace = tool(ctx, "read_memory", {"kind":"world"})
    assert trace.success and "REVISION_SHA256: ABSENT" in out and "NOT_FOUND" in out
    out, trace = tool(ctx, "read_memory", {"kind":"bad"})
    assert not trace.success and "INVALID_MEMORY_KIND" in out


def test_update_race_preserves_external_bytes_and_cleans_snapshot(project):
    path = project.dir / "outline/summary.md"; old_rev = file_revision(path)
    def racing_factory(*args, **kwargs):
        snap = prepare_snapshot(*args, **kwargs)
        atomic_write_text(path, "# EXTERNAL B\n")
        return snap
    with pytest.raises(MutationError, match="STALE_REVISION"):
        MutationService(project, snapshot_factory=racing_factory).mutate(
            request("outline/summary.md", "# AI\n", old_rev))
    assert path.read_text(encoding="utf-8") == "# EXTERNAL B\n"
    assert list_history(project) == [] and not list((project.dir / ".history").glob("*.bak"))


def test_create_race_preserves_external_file(project):
    path = project.dir / "outline/volumes/vol009.md"
    def racing_factory(*args, **kwargs):
        snap = prepare_snapshot(*args, **kwargs)
        atomic_write_text(path, "# EXTERNAL CREATED\n")
        return snap
    with pytest.raises(MutationError, match="STALE_REVISION"):
        MutationService(project, snapshot_factory=racing_factory).mutate(
            request("outline/volumes/vol009.md", "# AI\n", ABSENT))
    assert path.read_text(encoding="utf-8") == "# EXTERNAL CREATED\n"
    assert list_history(project) == [] and not list((project.dir / ".history").glob("*.bak"))


@pytest.mark.parametrize("tool_name,root,prompt_args", [
    ("read_character", "characters", {"name":"泄漏"}),
    ("read_world", "world", {"name":"泄漏"}),
    ("search_memory", "memory", {"keyword":"OUTSIDE_SECRET"}),
])
def test_symlink_escape_never_leaks(project, tmp_path, tool_name, root, prompt_args):
    outside = tmp_path / f"outside-{root}.md"; outside.write_text("# 泄漏\nOUTSIDE_SECRET", encoding="utf-8")
    link = project.dir / root / "evil.md"
    try: link.symlink_to(outside)
    except OSError: pytest.skip("symlink unavailable")
    out, _ = tool(context(project), tool_name, prompt_args)
    assert "OUTSIDE_SECRET" not in out
    assert not any("OUTSIDE_SECRET" in h["snippet"] for h in search_knowledge(project, "OUTSIDE_SECRET"))


def test_outline_symlink_and_context_never_leak(project, tmp_path):
    outside = tmp_path / "outside-outline.md"; outside.write_text("OUTSIDE_OUTLINE", encoding="utf-8")
    link = project.dir / "outline/volumes/vol001.md"
    try: link.symlink_to(outside)
    except OSError: pytest.skip("symlink unavailable")
    out, _ = tool(context(project), "read_outline", {"volume":1})
    assert "OUTSIDE_OUTLINE" not in out
    assert all("OUTSIDE_OUTLINE" not in i.text for i in collect_project_context(project))


@pytest.mark.parametrize("name,root,tool_name", [("林小满","characters","update_character"),
                                                   ("修行体系","world","update_world")])
def test_identity_mismatch_has_zero_write_history(project, name, root, tool_name):
    path = project.dir / root / "entry.md"; atomic_write_text(path, f"# {name}\n原内容\n")
    before = path.read_bytes()
    out, trace = tool(context(project), tool_name, {"name":name,"text":"# 张三\n新内容", "expected_revision":file_revision(path)})
    assert not trace.success and "IDENTITY_MISMATCH" in out
    assert path.read_bytes() == before and list_history(project) == []


def test_noop_is_normal_result_and_zero_history(project):
    path = project.dir / "outline/summary.md"; text = path.read_text(encoding="utf-8")
    out, trace = tool(context(project), "update_outline", {"scope":"summary", "text":text,
                                                            "expected_revision":file_revision(path)})
    assert trace.success and out.startswith("NO_CHANGE") and list_history(project) == []


def test_context_world_and_explicit_recent_bounded(project):
    atomic_write_text(project.dir / "world/cultivation.md", "# 修行体系\n九境")
    # Reuse public chapter API for a valid confirmed source.
    from core.chapter import write_draft, confirm_draft
    write_draft(project, 1, "开篇", "RECENT_UNIQUE_MARKER" * 20); confirm_draft(project, 1)
    default = collect_project_context(project, world_names=["修行体系"])
    assert any(i.type == "WORLD" for i in default)
    assert all("RECENT_UNIQUE_MARKER" not in i.text for i in default)
    recent = collect_recent_chapters(project, 1, 80)
    assert recent and recent[0].type == "RECENT_CHAPTER" and sum(i.chars for i in recent) <= 80
    assert len(render_context_items(default + recent, 240)) <= 240


def test_doctor_utf8_missing_volume_and_json_shape(project):
    bad = project.dir / "characters/bad.md"; bad.write_bytes(b"\xff\xfe")
    issues = doctor(project)
    assert any(i["code"] == "INVALID_UTF8" and i["severity"] == "ERROR" for i in issues)
    assert any(i["code"] == "MISSING_CURRENT_VOLUME_OUTLINE" for i in issues)


def test_history_metadata_schema_rejects_non_object(project):
    idx = project.dir / ".history/index.jsonl"
    idx.write_text(json.dumps({"seq":1,"operation":"ai.x","timestamp":"x","changes":[
        {"target":"x","previous":"absent","backup":None}],"metadata":"bad"}) + "\n", encoding="utf-8")
    with pytest.raises(Exception, match="metadata"):
        list_history(project)


@pytest.mark.parametrize("wording", ["把总纲修改成 X", "让林小满会唱歌", "第一卷目标换成 X",
                                      "设定林小满为剑修", "adjust the outline to X"])
def test_weak_model_wording_never_mutates(project, wording):
    ctx = context(project); ctx.provider.config.tool_calls = False
    ctx.provider.replies = [ChatResult(text="当前模型无法安全执行写操作。")]
    before = {p.relative_to(project.dir).as_posix(): p.read_bytes() for p in project.dir.rglob("*") if p.is_file()}
    result = AgentSession(ctx).ask(wording)
    after = {p.relative_to(project.dir).as_posix(): p.read_bytes() for p in project.dir.rglob("*") if p.is_file()}
    assert result.status == "completed" and before == after and list_history(project) == []
    assert any("MUTATION_CAPABILITY: DISABLED" in m.content for m in ctx.provider.last_messages)


def response_tool(call_id, name, args):
    return {"choices":[{"message":{"role":"assistant","content":"","tool_calls":[{
        "id":call_id,"type":"function","function":{"name":name,"arguments":json.dumps(args, ensure_ascii=False)}}]},
        "finish_reason":"tool_calls"}],"model":"mock-model"}


def final_response(text="完成并复核。"):
    return {"choices":[{"message":{"role":"assistant","content":text},"finish_reason":"stop"}],"model":"mock-model"}


def test_local_http_memory_read_write_read_and_undo(tmp_path, server):
    pid = _create_project(tmp_path, "memory-http"); _write_settings(tmp_path, base_url=server.base_url)
    path = tmp_path / "novels" / pid / "memory/long_term.md"; before = path.read_bytes(); rev = file_revision(path)
    server.responses.extend([
        (200, response_tool("r1","read_memory",{"kind":"long_term"})),
        (200, response_tool("w1","save_memory_entry",{"kind":"long_term","text":"北门钥匙","expected_revision":rev})),
        (200, response_tool("r2","read_memory",{"kind":"long_term"})), (200, final_response())])
    out = _run(tmp_path, "chat", "把北门钥匙作为长期记忆保存。", "--project", pid, "--show-tools")
    assert out.returncode == 0 and "北门钥匙" in path.read_text(encoding="utf-8")
    assert "[tool] read_memory OK" in out.stdout and "save_memory_entry WRITE OK" in out.stdout
    assert _run(tmp_path, "undo-last-change", pid).returncode == 0 and path.read_bytes() == before


def test_local_http_natural_character_update_full_registry(tmp_path, server):
    pid = _create_project(tmp_path, "natural-http"); _write_settings(tmp_path, base_url=server.base_url)
    path = tmp_path / "novels" / pid / "characters/lin.md"; atomic_write_text(path, "# 林小满\n不会唱歌\n")
    rev = file_revision(path)
    server.responses.extend([
        (200, response_tool("r1","read_character",{"name":"林小满"})),
        (200, response_tool("w1","update_character",{"name":"林小满","text":"# 林小满\n以后会唱歌\n","expected_revision":rev})),
        (200, response_tool("r2","read_character",{"name":"林小满"})), (200, final_response())])
    out = _run(tmp_path, "chat", "让林小满以后会唱歌。", "--project", pid)
    assert out.returncode == 0 and "以后会唱歌" in path.read_text(encoding="utf-8")
    schemas = [x["function"]["name"] for x in server.requests[0]["body_json"]["tools"]]
    assert "read_world" in schemas and "read_memory" in schemas and "update_outline" in schemas


def test_local_http_ordinary_world_read_has_full_registry_and_no_write(tmp_path, server):
    pid = _create_project(tmp_path, "world-http"); _write_settings(tmp_path, base_url=server.base_url)
    path = tmp_path / "novels" / pid / "world/cultivation.md"; atomic_write_text(path, "# 修行体系\n九境\n")
    before = path.read_bytes()
    server.responses.extend([(200, response_tool("r1","read_world",{"name":"修行体系"})),
                             (200, final_response("共有九境。"))])
    out = _run(tmp_path, "chat", "修行体系有哪些境界？", "--project", pid)
    assert out.returncode == 0 and "九境" in out.stdout and path.read_bytes() == before
    assert not (tmp_path / "novels" / pid / ".history/index.jsonl").exists()


def test_cli_doctor_json_search_limit_revisions_and_metadata_corruption(tmp_path):
    pid = _create_project(tmp_path, "doctor-cli")
    root = tmp_path / "novels" / pid
    atomic_write_text(root / "outline/volumes/vol001.md", "# 第一卷\nneedle\nneedle\n")
    result = _run(tmp_path, "knowledge", "doctor", pid, "--json")
    assert result.returncode == 0 and json.loads(result.stdout)["status"] == "pass"
    result = _run(tmp_path, "knowledge", "search", pid, "needle", "--limit", "1")
    assert result.returncode == 0 and len(result.stdout.strip().splitlines()) == 1
    result = _run(tmp_path, "knowledge", "revisions", pid)
    assert result.returncode == 0 and "FACT_SOURCE" in result.stdout
    (root / "project.json").write_text("{broken", encoding="utf-8")
    result = _run(tmp_path, "knowledge", "doctor", pid, "--json")
    payload = json.loads(result.stdout)
    assert result.returncode == 1 and payload["status"] == "error"
    assert payload["issues"][0]["code"] == "PROJECT_METADATA"


def test_memory_stale_revision_is_rejected(project):
    ctx = context(project); path = project.dir / "memory/long_term.md"; before = path.read_bytes()
    out, trace = tool(ctx, "save_memory_entry", {"kind":"long_term", "text":"new", "expected_revision":"bad"})
    assert not trace.success and "STALE_REVISION" in out
    assert path.read_bytes() == before and list_history(project) == []
