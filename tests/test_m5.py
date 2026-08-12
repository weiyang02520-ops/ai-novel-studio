"""M5 Writer: task cards, bounded context, streaming, draft safety, and CLI surface."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from adapters.cli.main import build_parser
from adapters.cli.m5 import _public_task_card
from agents.task_card import TaskCardError, WritingTaskCard, parse_task_card
from agents.planner import ChiefPlanningService
from agents.writer import WriterError, WriterRequest, WriterRunner
from core.ai_draft import AIChapterDraftService, AIDraftError
from core.chapter import confirm_draft, draft_path, parse_frontmatter, write_draft
from core.config import ModelConfig
from core.context import ContextItem, collect_project_context, collect_recent_chapters
from core.context_budget import ContextBudgetError, plan_context, render_writer_context
from core.generation import GenerationWorkspace, merge_continuation
from core.history import list_history, undo_last
from core.history import prepare_snapshot
from core.knowledge import doctor
from core.mutation import ABSENT, file_revision
from core.project import create_project
from core.relevance import RelevanceError, resolve_relevant_entities
from core.storage import DataIntegrityError, ProjectStore, atomic_write_text
from core.write_workflow import WriteRequest, WriteWorkflow, WriteWorkflowError
from llm.provider import BaseProvider, CONTEXT_TOO_LONG, ProviderError, STREAM_INTERRUPTED
from llm.types import ChatChunk, ChatResult, Usage


@pytest.fixture
def project(tmp_path):
    project = create_project(ProjectStore(tmp_path / "novels"), "M5", project_id="m5-novel")
    atomic_write_text(project.dir / "outline/summary.md", "# 总纲\n鹤梁山契约之谜。")
    atomic_write_text(project.dir / "outline/volumes/vol001.md", "# 第一卷\n进入鹤梁山。")
    atomic_write_text(project.dir / "outline/chapters/ch0001.md", "# 第一章\n沈砚发现无名尸体。")
    atomic_write_text(project.dir / "rules/writing_rules.md", "# 规则\n第三人称，克制。")
    atomic_write_text(project.dir / "characters/shen-yan.md", "# 沈砚\n冷静的验尸人。")
    atomic_write_text(project.dir / "world/contract.md", "# 契纹\n死者身上的契约印记。")
    return project


def card(**overrides):
    data = {"chapter": 1, "goal": "发现尸体", "target_chars": 1000,
            "characters": ["沈砚"], "world_elements": ["契纹"]}
    data.update(overrides)
    return WritingTaskCard(**data)


class FakeProvider(BaseProvider):
    def __init__(self, *, chats=None, streams=None, max_context=16000, model="fake-model"):
        super().__init__(ModelConfig(provider="openai_compatible", base_url="http://localhost/v1",
                                     model=model, max_context_tokens=max_context))
        self.chats = list(chats or [])
        self.streams = list(streams or [])
        self.chat_calls = []
        self.stream_calls = []

    def chat(self, messages, *, temperature=None, tools=None):
        self.chat_calls.append(messages)
        value = self.chats.pop(0)
        return value

    def stream_chat(self, messages, *, temperature=None, tools=None):
        self.stream_calls.append(messages)
        events = self.streams.pop(0)
        for event in events:
            if isinstance(event, BaseException):
                raise event
            if callable(event):
                event()
                continue
            yield event


def planner_result(text=None):
    text = text or json.dumps(card().to_dict(), ensure_ascii=False)
    return ChatResult(text=text, model="chief", usage=Usage(10, 2, 12))


def settings():
    return SimpleNamespace(context={"reserve_output_tokens": 1000,
                                    "max_recent_chapters": 5,
                                    "max_recent_text_chars": 3000})


def workflow(project, streams, chats=None, max_context=16000):
    chief = FakeProvider(chats=chats or [planner_result()], model="chief")
    writer = FakeProvider(streams=streams, max_context=max_context, model="writer")
    return WriteWorkflow(chief_provider=chief, writer_provider=writer,
                         chief_prompt="JSON only", writer_prompt="prose only", settings=settings()), chief, writer


def test_task_card_raw_fenced_and_deterministic_hash():
    raw = json.dumps(card().to_dict(), ensure_ascii=False)
    assert parse_task_card(raw).task_hash == parse_task_card(f"```json\n{raw}\n```").task_hash


@pytest.mark.parametrize("raw", ["[]", '{"chapter":"1","goal":"x","target_chars":1000}',
                                  '{"chapter":1,"goal":"x","target_chars":10}'])
def test_task_card_rejects_invalid_shapes(raw):
    with pytest.raises(TaskCardError):
        parse_task_card(raw)


def test_planner_repairs_then_falls_back_without_persisting_raw_output():
    valid = json.dumps(card().to_dict(), ensure_ascii=False)
    repaired_provider = FakeProvider(chats=[planner_result("bad"), planner_result(valid)])
    repaired = ChiefPlanningService(repaired_provider, "json").plan(
        chapter=1, target_chars=1000, title="", instruction="", project_data="facts")
    assert repaired.repaired and repaired.card.source == "structured"
    fallback_provider = FakeProvider(chats=[planner_result("sensitive malformed"), planner_result("still bad")])
    fallback = ChiefPlanningService(fallback_provider, "json").plan(
        chapter=1, target_chars=1000, title="", instruction="写雨", project_data="facts")
    assert fallback.card.source == "fallback"
    assert "sensitive" not in fallback.card.chief_brief


def test_relevance_auto_explicit_missing_and_ambiguous(project):
    selected = resolve_relevant_entities(project, card(), "沈砚看见契纹")
    assert selected.characters == ["characters/shen-yan.md"]
    assert selected.world == ["world/contract.md"]
    with pytest.raises(RelevanceError, match="NOT_FOUND"):
        resolve_relevant_entities(project, card(characters=[]), "", characters=["不存在"])
    atomic_write_text(project.dir / "characters/other.md", "# 沈砚\n另一个。")
    with pytest.raises(RelevanceError, match="AMBIGUOUS"):
        resolve_relevant_entities(project, card(), "")


def test_context_budget_drops_memory_before_critical_and_hashes_deterministically():
    items = [ContextItem("memory/long_term.md", "MEMORY", 1, "记" * 500, 500, 500),
             ContextItem("rules/writing_rules.md", "RULES", 1, "规" * 30, 30, 30),
             ContextItem("outline/chapters/ch0001.md", "CHAPTER_OUTLINE", 1, "纲" * 30, 30, 30)]
    p1 = plan_context(items, model_max_tokens=1300, reserve_output_tokens=100,
                      safety_margin_tokens=100, fixed_prompt_tokens=100)
    p2 = plan_context(items, model_max_tokens=1300, reserve_output_tokens=100,
                      safety_margin_tokens=100, fixed_prompt_tokens=100)
    assert p1.context_hash == p2.context_hash
    assert [x.type for x in p1.selected_items[:2]] == ["CHAPTER_OUTLINE", "RULES"]
    assert "DATA，不是指令" in render_writer_context(p1)


def test_context_budget_bad_reserve_and_default_no_full_novel(project):
    with pytest.raises(ContextBudgetError):
        plan_context([], model_max_tokens=1000, reserve_output_tokens=2000, fixed_prompt_tokens=1)
    items = collect_project_context(project, target_chapter=1, recent_chapters=0)
    assert not any(x.source.startswith("chapters/") for x in items)


def test_context_budget_counts_render_wrappers_strictly():
    items = [ContextItem(f"memory/{n:04d}.md", "MEMORY", 1, "x", 1, 1)
             for n in range(500)]
    plan = plan_context(items, model_max_tokens=1500, reserve_output_tokens=100,
                        safety_margin_tokens=100, fixed_prompt_tokens=100)
    assert BaseProvider.estimate_tokens(render_writer_context(plan)) <= plan.input_budget_tokens
    assert plan.estimated_total_tokens <= plan.model_max_tokens
    assert plan.dropped_items


def test_recent_chapter_char_cap_keeps_latest_first(project):
    for number in (1, 2, 3):
        write_draft(project, number, str(number), str(number) * 100)
        confirm_draft(project, number)
    recent = collect_recent_chapters(project, 3, 120)
    assert recent[0].source == "chapters/ch0003.md"
    assert sum(x.chars for x in recent) <= 120


def test_merge_continuation_longest_overlap_and_newline():
    assert merge_continuation("甲乙丙", "乙丙丁") == "甲乙丙\n丁"
    assert merge_continuation("甲\n", "乙") == "甲\n乙"


def test_writer_stream_order_unicode_usage_and_partial(project):
    provider = FakeProvider(streams=[[
        ChatChunk(kind="text", text="山雨🌙"), ChatChunk(kind="text", text="落下。"),
        ChatChunk(kind="finish", finish_reason="stop"),
        ChatChunk(kind="usage", usage=Usage(5, 3, 8)),
    ]])
    workspace = GenerationWorkspace(project, 1)
    workspace.prepare({"mode": "new"})
    context = plan_context([], model_max_tokens=3000, reserve_output_tokens=500,
                           safety_margin_tokens=100, fixed_prompt_tokens=10)
    result = WriterRunner(provider, "prose").run(
        WriterRequest(project, 1, "", card(), context, 1000, "new"),
        rendered_context=render_writer_context(context), workspace=workspace)
    assert result.text == workspace.text() == "山雨🌙落下。"
    assert result.usage.total_tokens == 8
    assert result.generation_state == "complete"


def test_writer_length_empty_tool_and_no_retry_after_text(project):
    context = plan_context([], model_max_tokens=3000, reserve_output_tokens=500,
                           safety_margin_tokens=100, fixed_prompt_tokens=10)
    request = WriterRequest(project, 1, "", card(), context, 1000, "new")
    length = FakeProvider(streams=[[ChatChunk(kind="text", text="正文"),
                                    ChatChunk(kind="finish", finish_reason="length")]])
    assert WriterRunner(length, "p").run(request, rendered_context="x").generation_state == "truncated"
    with pytest.raises(WriterError, match="EMPTY_WRITER_OUTPUT"):
        WriterRunner(FakeProvider(streams=[[]]), "p").run(request, rendered_context="x")
    tool = FakeProvider(streams=[[ChatChunk(kind="tool_call")]])
    with pytest.raises(WriterError, match="WRITER_PROTOCOL_ERROR"):
        WriterRunner(tool, "p").run(request, rendered_context="x")
    interrupted = FakeProvider(streams=[[ChatChunk(kind="text", text="沈砚"),
                                         ProviderError(STREAM_INTERRUPTED, "gone")]])
    result = WriterRunner(interrupted, "p").run(request, rendered_context="x")
    assert result.generation_state == "interrupted"
    assert len(interrupted.stream_calls) == 1
    text_then_tool = FakeProvider(streams=[[ChatChunk(kind="text", text="正文"),
                                            ChatChunk(kind="tool_call")]])
    with pytest.raises(WriterError, match="WRITER_PROTOCOL_ERROR"):
        WriterRunner(text_then_tool, "p").run(request, rendered_context="x")


def test_ai_draft_create_provenance_history_undo_and_confirm_block(project):
    result = AIChapterDraftService(project).finalize(
        chapter=1, title="雨", body="山雨落下。", mode="new", generation_state="complete",
        model="writer", context_hash="c" * 64, task_hash="t" * 64, expected_revision=ABSENT,
        characters=["沈砚"])
    metadata, body = parse_frontmatter(draft_path(project, 1).read_text(encoding="utf-8"))
    assert metadata["origin"] == "ai" and metadata["status"] == "draft"
    assert metadata["generation_mode"] == "new" and body == "山雨落下。"
    assert list_history(project)[-1]["operation"] == "ai.draft.create"
    with pytest.raises(DataIntegrityError):
        confirm_draft(project, 1)
    undo_last(project)
    assert not draft_path(project, 1).exists()


def test_ai_draft_rewrite_continue_revision_and_manual_protection(project):
    service = AIChapterDraftService(project)
    first = service.finalize(chapter=1, title="", body="A", mode="new", generation_state="complete",
                             model="m", context_hash="c", task_hash="t", expected_revision=ABSENT)
    second = service.finalize(chapter=1, title="", body="B", mode="rewrite", generation_state="complete",
                              model="m", context_hash="c2", task_hash="t2", expected_revision=first.revision)
    service.finalize(chapter=1, title="", body="B\nC", mode="continue", generation_state="truncated",
                     model="m", context_hash="c3", task_hash="t3", expected_revision=second.revision)
    assert parse_frontmatter(draft_path(project, 1).read_text(encoding="utf-8"))[1] == "B\nC"
    with pytest.raises(AIDraftError, match="STALE_DRAFT_REVISION"):
        service.finalize(chapter=1, title="", body="X", mode="rewrite", generation_state="complete",
                         model="m", context_hash="c", task_hash="t", expected_revision="stale")
    draft_path(project, 1).unlink()
    write_draft(project, 1, "", "manual")
    with pytest.raises(AIDraftError, match="MANUAL_DRAFT_PROTECTED"):
        service.finalize(chapter=1, title="", body="X", mode="rewrite", generation_state="complete",
                         model="m", context_hash="c", task_hash="t",
                         expected_revision=file_revision(draft_path(project, 1)))


def test_ai_draft_write_failure_restores_old_bytes(project):
    service = AIChapterDraftService(project)
    first = service.finalize(chapter=1, title="", body="OLD", mode="new", generation_state="complete",
                             model="m", context_hash="c", task_hash="t", expected_revision=ABSENT)
    old = draft_path(project, 1).read_bytes()
    def broken_writer(path, text):
        atomic_write_text(path, text)
        raise OSError("synthetic")
    with pytest.raises(AIDraftError, match="DRAFT_WRITE_FAILED"):
        AIChapterDraftService(project, writer=broken_writer).finalize(
            chapter=1, title="", body="NEW", mode="rewrite", generation_state="complete",
            model="m", context_hash="c", task_hash="t", expected_revision=first.revision)
    assert draft_path(project, 1).read_bytes() == old


def test_ai_draft_snapshot_failure_and_new_overwrite_block(project):
    def failed_snapshot(*args, **kwargs):
        raise OSError("synthetic")
    with pytest.raises(AIDraftError, match="DRAFT_SNAPSHOT_FAILED"):
        AIChapterDraftService(project, snapshot_factory=failed_snapshot).finalize(
            chapter=1, title="", body="X", mode="new", generation_state="complete",
            model="m", context_hash="c", task_hash="t", expected_revision=ABSENT)
    assert not draft_path(project, 1).exists()
    created = AIChapterDraftService(project).finalize(
        chapter=1, title="", body="A", mode="new", generation_state="complete",
        model="m", context_hash="c", task_hash="t", expected_revision=ABSENT)
    with pytest.raises(AIDraftError, match="AI_DRAFT_EXISTS"):
        AIChapterDraftService(project).finalize(
            chapter=1, title="", body="B", mode="new", generation_state="complete",
            model="m", context_hash="c", task_hash="t", expected_revision=created.revision)


def test_ai_draft_history_commit_failure_rolls_back(project):
    class CommitFailSnapshot:
        def __init__(self, inner):
            self.inner, self.seq = inner, inner.seq
        def commit(self): raise OSError("commit failed")
        def restore(self): return self.inner.restore()
        def discard(self): return self.inner.discard()
    def factory(*args, **kwargs):
        return CommitFailSnapshot(prepare_snapshot(*args, **kwargs))
    with pytest.raises(AIDraftError, match="DRAFT_WRITE_FAILED"):
        AIChapterDraftService(project, snapshot_factory=factory).finalize(
            chapter=1, title="", body="X", mode="new", generation_state="complete",
            model="m", context_hash="c", task_hash="t", expected_revision=ABSENT)
    assert not draft_path(project, 1).exists() and not list_history(project)


def test_ai_confirm_block_even_if_user_confirmed(project):
    AIChapterDraftService(project).finalize(
        chapter=1, title="", body="X", mode="new", generation_state="complete",
        model="m", context_hash="c", task_hash="t", expected_revision=ABSENT)
    path = draft_path(project, 1)
    atomic_write_text(path, path.read_text(encoding="utf-8").replace("status: draft", "status: user_confirmed"))
    with pytest.raises(DataIntegrityError):
        confirm_draft(project, 1)


def test_workflow_new_plan_context_stream_finalize(project):
    flow, chief, writer = workflow(project, [[ChatChunk(kind="text", text="山雨落下。"),
                                              ChatChunk(kind="finish", finish_reason="stop")]])
    stages = []
    result = flow.run(WriteRequest(project, target_chars=1000), on_stage=stages.append)
    assert result.status == "saved"
    assert stages == ["Planning", "Context", "Writing", "Saving"]
    assert project.current_chapter == 0
    metadata, _ = parse_frontmatter(draft_path(project, 1).read_text(encoding="utf-8"))
    assert metadata["origin"] == "ai"
    writer_prompt = writer.stream_calls[0][1].content
    assert "TASK_CARD" in writer_prompt and "rules/writing_rules.md" in writer_prompt
    assert "characters/shen-yan.md" in writer_prompt and "world/contract.md" in writer_prompt


def test_workflow_plan_only_has_zero_mutation(project):
    flow, _, writer = workflow(project, [])
    result = flow.run(WriteRequest(project, target_chars=1000, plan_only=True))
    assert result.status == "planned"
    assert not draft_path(project, 1).exists()
    assert not list_history(project)
    assert not writer.stream_calls


def test_workflow_refuses_to_overwrite_existing_partial(project):
    workspace = GenerationWorkspace(project, 1)
    workspace.prepare({"mode": "new", "task_card": card().to_dict()})
    workspace.append("valuable partial")
    flow, _, writer = workflow(project, [[ChatChunk(kind="text", text="new")]])
    with pytest.raises(FileExistsError, match="PARTIAL_EXISTS"):
        flow.run(WriteRequest(project, target_chars=1000))
    assert workspace.text() == "valuable partial"
    assert not writer.stream_calls


def test_workflow_context_too_long_retries_once(project):
    flow, _, writer = workflow(project, [
        [ProviderError(CONTEXT_TOO_LONG, "too long")],
        [ChatChunk(kind="text", text="正文"), ChatChunk(kind="finish", finish_reason="stop")],
    ])
    result = flow.run(WriteRequest(project, target_chars=1000))
    assert result.status == "saved" and len(writer.stream_calls) == 2
    first = BaseProvider.estimate_messages_tokens(writer.stream_calls[0])
    second = BaseProvider.estimate_messages_tokens(writer.stream_calls[1])
    assert second < first


def test_workflow_interruption_keeps_partial_no_canonical(project):
    flow, _, _ = workflow(project, [[ChatChunk(kind="text", text="部分正文"),
                                      ProviderError(STREAM_INTERRUPTED, "gone")]])
    result = flow.run(WriteRequest(project, target_chars=1000))
    assert result.status == "interrupted"
    assert GenerationWorkspace(project, 1).text() == "部分正文"
    assert not draft_path(project, 1).exists()


def test_workflow_resume_merges_and_cleans(project):
    partial_flow, _, _ = workflow(project, [[ChatChunk(kind="text", text="山雨落下。"),
                                              ProviderError(STREAM_INTERRUPTED, "gone")]])
    partial_flow.run(WriteRequest(project, target_chars=1000))
    resume_flow, _, _ = workflow(project, [[ChatChunk(kind="text", text="下。他抬起头。"),
                                             ChatChunk(kind="finish", finish_reason="stop")]], chats=[])
    result = resume_flow.run(WriteRequest(project, chapter=1, mode="resume"))
    body = parse_frontmatter(draft_path(project, 1).read_text(encoding="utf-8"))[1]
    assert result.status == "saved" and body == "山雨落下。\n他抬起头。"
    assert not GenerationWorkspace(project, 1).partial.exists()


def test_workflow_resume_rejects_stale_canonical(project):
    initial = AIChapterDraftService(project).finalize(
        chapter=1, title="", body="A", mode="new", generation_state="complete",
        model="m", context_hash="c", task_hash="t", expected_revision=ABSENT)
    workspace = GenerationWorkspace(project, 1)
    workspace.prepare({"mode": "continue", "title": "", "base_revision": initial.revision,
                       "task_hash": card().task_hash, "context_hash": "c", "model": "m",
                       "task_card": card().to_dict()})
    workspace.append("partial")
    atomic_write_text(draft_path(project, 1), draft_path(project, 1).read_text(encoding="utf-8") + "external")
    flow, _, writer = workflow(project, [])
    with pytest.raises(WriteWorkflowError, match="STALE_DRAFT_REVISION"):
        flow.run(WriteRequest(project, chapter=1, mode="resume"))
    assert workspace.text() == "partial" and not writer.stream_calls


def test_continue_interruption_resume_keeps_original_canonical(project):
    first = AIChapterDraftService(project).finalize(
        chapter=1, title="", body="原有正文。", mode="new", generation_state="complete",
        model="m", context_hash="c", task_hash="t", expected_revision=ABSENT)
    flow1, _, _ = workflow(project, [[ChatChunk(kind="text", text="续写一。"),
                                      ProviderError(STREAM_INTERRUPTED, "gone")]])
    interrupted = flow1.run(WriteRequest(project, chapter=1, mode="continue", target_chars=1000))
    assert interrupted.status == "interrupted"
    assert file_revision(draft_path(project, 1)) == first.revision
    flow2, _, _ = workflow(project, [[ChatChunk(kind="text", text="续写二。"),
                                      ChatChunk(kind="finish", finish_reason="stop")]], chats=[])
    flow2.run(WriteRequest(project, chapter=1, mode="resume"))
    body = parse_frontmatter(draft_path(project, 1).read_text(encoding="utf-8"))[1]
    assert body == "原有正文。\n续写一。\n续写二。"


def test_rewrite_interruption_resume_replaces_original_canonical(project):
    AIChapterDraftService(project).finalize(
        chapter=1, title="", body="旧正文。", mode="new", generation_state="complete",
        model="m", context_hash="c", task_hash="t", expected_revision=ABSENT)
    flow1, _, _ = workflow(project, [[ChatChunk(kind="text", text="新正文一。"),
                                      ProviderError(STREAM_INTERRUPTED, "gone")]])
    flow1.run(WriteRequest(project, chapter=1, mode="rewrite", target_chars=1000))
    flow2, _, _ = workflow(project, [[ChatChunk(kind="text", text="新正文二。"),
                                      ChatChunk(kind="finish", finish_reason="stop")]], chats=[])
    flow2.run(WriteRequest(project, chapter=1, mode="resume"))
    body = parse_frontmatter(draft_path(project, 1).read_text(encoding="utf-8"))[1]
    assert body == "新正文一。\n新正文二。"
    assert "旧正文" not in body


def test_workflow_protects_manual_and_requires_plan(project):
    write_draft(project, 1, "", "manual")
    flow, _, writer = workflow(project, [])
    with pytest.raises(WriteWorkflowError, match="MANUAL_DRAFT_PROTECTED"):
        flow.run(WriteRequest(project, mode="rewrite"))
    assert not writer.stream_calls
    draft_path(project, 1).unlink()
    (project.dir / "outline/chapters/ch0001.md").unlink()
    with pytest.raises(WriteWorkflowError, match="INSUFFICIENT_WRITING_PLAN"):
        flow.run(WriteRequest(project))


@pytest.mark.parametrize("mode", ["new", "rewrite", "continue"])
def test_workflow_generation_race_external_bytes_win(project, mode):
    if mode != "new":
        AIChapterDraftService(project).finalize(
            chapter=1, title="", body="OLD", mode="new", generation_state="complete",
            model="m", context_hash="c", task_hash="t", expected_revision=ABSENT)
    target = draft_path(project, 1)
    external = target.read_bytes() + b"EXTERNAL" if target.exists() else None
    def mutate():
        if mode == "new":
            write_draft(project, 1, "external", "USER")
        else:
            target.write_bytes(external)
    flow, _, _ = workflow(project, [[ChatChunk(kind="text", text="generated"), mutate,
                                      ChatChunk(kind="finish", finish_reason="stop")]])
    with pytest.raises(AIDraftError, match="STALE_DRAFT_REVISION"):
        flow.run(WriteRequest(project, chapter=1, mode=mode, target_chars=1000))
    assert target.read_bytes() == (external if mode != "new" else target.read_bytes())
    if mode == "new":
        assert b"USER" in target.read_bytes()


def test_doctor_flags_invalid_ai_state(project):
    AIChapterDraftService(project).finalize(
        chapter=1, title="", body="正文", mode="new", generation_state="complete",
        model="m", context_hash="c", task_hash="t", expected_revision=ABSENT)
    path = draft_path(project, 1)
    text = path.read_text(encoding="utf-8").replace("generation_state: complete", "generation_state: broken")
    atomic_write_text(path, text)
    assert any(x["code"] == "AI_DRAFT_INVALID_GENERATION_STATE" for x in doctor(project))


def test_m5_cli_parser_surface_and_exclusion():
    args = build_parser().parse_args(["write", "novel", "2", "--continue", "--character", "沈砚"])
    assert args.chapter == 2 and args.continue_mode and args.character == ["沈砚"]
    args = build_parser().parse_args(["context", "plan", "novel", "--json"])
    assert args.context_command == "plan" and args.json
    args = build_parser().parse_args(["draft", "partial", "discard", "novel", "2"])
    assert args.partial_command == "discard" and args.chapter == 2


def test_public_task_card_does_not_echo_chief_brief():
    shown = _public_task_card(card(chief_brief="opaque raw response"))
    assert shown["goal"] == "发现尸体"
    assert "chief_brief" not in shown
