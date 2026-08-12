"""M3 Agent Runtime 测试(FakeProvider 注入, 不联网)。

覆盖: 纯文本 / 单工具 / 多工具同轮 / 多轮 / 无效 JSON 修正 / 未知工具 / 未授权 /
tool limit preflight / round limit / runaway / Provider 401 / 消息序列 /
weak-model fallback(不传 tools + bounded context) / session 两轮 / 裁剪。
"""
from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

import pytest  # noqa: E402

from agents.context import build_fallback_context  # noqa: E402
from agents.definitions import chief_agent_def  # noqa: E402
from agents.runtime import AgentSession, SESSION_MAX_MESSAGES  # noqa: E402
from agents.types import AgentContext  # noqa: E402
from core.config import ModelConfig, Settings  # noqa: E402
from core.project import create_project  # noqa: E402
from core.storage import ProjectStore  # noqa: E402
from llm.provider import AUTH_ERROR, ProviderError  # noqa: E402
from llm.testing import FakeProvider  # noqa: E402
from llm.types import ChatMessage, ChatResult, ToolCall  # noqa: E402
from tools.read_tools import build_readonly_registry  # noqa: E402


def make_context(tmp_path, *, tool_calls=True, max_tool_calls=8, max_rounds=4,
                 project_id="rt-novel"):
    store = ProjectStore(tmp_path / "novels")
    proj = create_project(store, "RT 小说", project_id=project_id)
    cfg = ModelConfig(base_url="http://127.0.0.1:9", model="m", tool_calls=tool_calls)
    agent = chief_agent_def()
    if max_rounds != 4:
        agent.max_tool_rounds = max_rounds
    registry = build_readonly_registry()
    provider = FakeProvider(cfg)
    settings = Settings.load(Path("x"))
    return AgentContext(project=proj, settings=settings, provider=provider,
                        tool_registry=registry, agent_def=agent,
                        max_tool_calls=max_tool_calls)


def make_session(ctx, replies):
    ctx.provider.replies = list(replies)
    return AgentSession(ctx)


def tc(call_id, name, args="{}"):
    return ToolCall(id=call_id, name=name, arguments_json=args)


# ── 纯文本完成(§113) ─────────────────────────────────────

def test_pure_text_completed(tmp_path):
    ctx = make_context(tmp_path)
    s = make_session(ctx, [ChatResult(text="你好，我是主编。", model="m")])
    r = s.ask("你是谁")
    assert r.status == "completed"
    assert r.text == "你好，我是主编。"
    assert r.rounds == 0 and r.tool_calls_count == 0
    assert len(r.calls) == 1


# ── 单工具(§114) ─────────────────────────────────────────

def test_one_tool_then_text(tmp_path):
    ctx = make_context(tmp_path)
    s = make_session(ctx, [
        ChatResult(text="", tool_calls=[tc("c1", "project_info")], model="m"),
        ChatResult(text="项目存在。", model="m"),
    ])
    r = s.ask("项目状态?")
    assert r.status == "completed"
    assert r.text == "项目存在。"
    assert r.tool_calls_count == 1
    assert len(r.tool_trace) == 1 and r.tool_trace[0].name == "project_info"
    assert r.tool_trace[0].success
    # 消息序列: system → user → assistant(tool_calls) → tool(result) → assistant(最终)
    msgs = s.messages
    assert msgs[0].role == "user"
    assert msgs[1].role == "assistant" and msgs[1].tool_calls is not None
    assert msgs[2].role == "tool" and msgs[2].tool_call_id == "c1"
    assert msgs[3].role == "assistant" and msgs[3].content == "项目存在。"
    # 消息序列含 system(§23): provider 收到的第一条是 system
    assert ctx.provider.last_messages[0].role == "system"
    assert ctx.provider.last_messages[0].content == ctx.agent_def.system_prompt


# ── 多工具同轮(§27, 115) ─────────────────────────────────

def test_multiple_tools_same_round(tmp_path):
    ctx = make_context(tmp_path)
    s = make_session(ctx, [
        ChatResult(text="", tool_calls=[tc("c1", "project_info"), tc("c2", "list_chapters")], model="m"),
        ChatResult(text="两个工具都执行了。", model="m"),
    ])
    r = s.ask("整体情况?")
    assert r.status == "completed"
    assert r.tool_calls_count == 2
    assert [t.name for t in r.tool_trace] == ["project_info", "list_chapters"]  # 保持模型顺序
    # tool 消息 tool_call_id 与调用对应(§26)
    tool_msgs = [m for m in s.messages if m.role == "tool"]
    assert [m.tool_call_id for m in tool_msgs] == ["c1", "c2"]


# ── 多工具轮次(§116) ─────────────────────────────────────

def test_multiple_tool_rounds(tmp_path):
    ctx = make_context(tmp_path)
    s = make_session(ctx, [
        ChatResult(text="", tool_calls=[tc("c1", "list_chapters")], model="m"),
        ChatResult(text="", tool_calls=[tc("c2", "read_outline", '{"volume": 1}')], model="m"),
        ChatResult(text="完成。", model="m"),
    ])
    r = s.ask("先章节再大纲")
    assert r.status == "completed"
    assert r.rounds == 2
    assert r.tool_calls_count == 2
    assert r.tool_trace[0].name == "list_chapters" and r.tool_trace[1].name == "read_outline"


# ── 无效 JSON → 模型修正(§40, 117) ───────────────────────

def test_invalid_json_then_corrected(tmp_path):
    ctx = make_context(tmp_path)
    s = make_session(ctx, [
        ChatResult(text="", tool_calls=[tc("c1", "read_character", '{"name": {bad')], model="m"),
        ChatResult(text="", tool_calls=[tc("c2", "read_character", '{"name": "林小满"}')], model="m"),
        ChatResult(text="读到了。", model="m"),
    ])
    r = s.ask("查人物")
    assert r.status == "completed"
    assert len(r.tool_trace) == 2
    assert not r.tool_trace[0].success
    assert "INVALID_TOOL_ARGUMENTS" in r.tool_trace[0].error
    assert r.tool_trace[1].success


# ── 未知工具(§36, 118) ───────────────────────────────────

def test_unknown_tool_rejected(tmp_path):
    ctx = make_context(tmp_path)
    s = make_session(ctx, [
        ChatResult(text="", tool_calls=[tc("c1", "run_shell", '{"cmd": "ls"}')], model="m"),
        ChatResult(text="知道了。", model="m"),
    ])
    r = s.ask("随便")
    assert r.status == "completed"
    assert not r.tool_trace[0].success
    assert "TOOL_NOT_FOUND" in r.tool_trace[0].error


# ── 未授权工具(§37, 119) ─────────────────────────────────

def test_unauthorized_tool_denied(tmp_path):
    """即使注册了写工具, Chief 白名单外也拒绝; 磁盘 0 修改。"""
    from tools.types import ToolDef
    ctx = make_context(tmp_path)
    wrote = {"called": False}

    def fake_write(ctx2, args):
        wrote["called"] = True
        return "written"

    ctx.tool_registry.register(ToolDef(name="write_chapter_draft", description="x",
                                       parameters={"type": "object", "properties": {}, "required": []},
                                       handler=fake_write, read_only=False))
    s = make_session(ctx, [
        ChatResult(text="", tool_calls=[tc("c1", "write_chapter_draft", '{"n": 1}')], model="m"),
        ChatResult(text="好。", model="m"),
    ])
    r = s.ask("写一章")
    assert r.status == "completed"
    assert not r.tool_trace[0].success
    assert "TOOL_PERMISSION_DENIED" in r.tool_trace[0].error
    assert wrote["called"] is False
    # 磁盘 0 修改
    drafts = ctx.project.store.safe_path(ctx.project.id, "drafts")
    assert not drafts.exists() or not list(drafts.iterdir())


# ── tool 总量 limit preflight(§30, 120) ───────────────────

def test_tool_call_limit_batch_preflight(tmp_path):
    ctx = make_context(tmp_path, max_tool_calls=2)
    s = make_session(ctx, [
        ChatResult(text="", tool_calls=[tc("c1", "project_info"), tc("c2", "list_chapters"),
                                        tc("c3", "read_outline")], model="m"),
    ])
    r = s.ask("三个工具")
    assert r.status == "tool_limit_exceeded"
    assert r.tool_calls_count == 0, "整批拒绝: 0 个工具执行"
    assert r.tool_trace == []
    assert s.messages[-1].role == "user"  # 未追加 assistant/tool 消息


def test_tool_call_limit_split_rounds(tmp_path):
    """第一轮 1 个工具(剩 1), 第二轮 2 个 → 整批拒绝。"""
    ctx = make_context(tmp_path, max_tool_calls=2)
    s = make_session(ctx, [
        ChatResult(text="", tool_calls=[tc("c1", "project_info")], model="m"),
        ChatResult(text="", tool_calls=[tc("c2", "list_chapters"), tc("c3", "read_outline")], model="m"),
    ])
    r = s.ask("分批")
    assert r.status == "tool_limit_exceeded"
    assert r.tool_calls_count == 1  # 第一轮已执行
    assert [t.name for t in r.tool_trace] == ["project_info"]  # 第二轮整批拒绝


# ── round limit / runaway(§31, 121) ──────────────────────

def test_round_limit_exceeded(tmp_path):
    ctx = make_context(tmp_path, max_rounds=2)
    s = make_session(ctx, [
        ChatResult(text="", tool_calls=[tc("c1", "project_info")], model="m"),
        ChatResult(text="", tool_calls=[tc("c2", "project_info")], model="m"),
        ChatResult(text="", tool_calls=[tc("c3", "project_info")], model="m"),
    ])
    r = s.ask("无限工具")
    assert r.status == "round_limit_exceeded"
    assert r.rounds == 2
    assert r.tool_calls_count == 2  # 前两轮执行, 第三轮整批拒绝


def test_runaway_fake_provider_terminates(tmp_path):
    """FakeProvider 无限返回 tool_calls → Runtime 必须终止(不无限烧 API)。"""
    ctx = make_context(tmp_path, max_rounds=4)
    ctx.provider.replies = [ChatResult(text="", tool_calls=[tc(f"c{i}", "project_info")], model="m")
                            for i in range(50)]  # 50 条, 远超上限
    s = AgentSession(ctx)
    r = s.ask("无限")
    assert r.status == "round_limit_exceeded"
    assert r.rounds == 4
    assert len(r.calls) == 5  # 4 轮工具 + 1 次被拒前的调用


# ── Provider 401(§32, 122) ───────────────────────────────

def test_provider_auth_error_safe(tmp_path):
    ctx = make_context(tmp_path)
    s = make_session(ctx, [ProviderError(AUTH_ERROR, "API Key 无效或未授权。")])
    r = s.ask("你好")
    assert r.status == "provider_error"
    assert r.error_code == AUTH_ERROR
    assert "无效" in r.error_message


# ── weak model fallback(§80-86, 136-139) ──────────────────

def test_weak_model_no_tools_sent(tmp_path):
    ctx = make_context(tmp_path, tool_calls=False)
    s = make_session(ctx, [ChatResult(text="基于上下文回答。", model="m")])
    r = s.ask("项目状态")
    assert r.status == "completed"
    assert r.tool_calls_count == 0
    # 首轮注入 bounded pack(§85: user-level DATA block)
    assert ctx.provider.last_messages[0].role == "system"
    assert ctx.provider.last_messages[1].role == "user"
    assert "[PROJECT_DATA_BEGIN]" in ctx.provider.last_messages[1].content
    assert "[PROJECT_DATA_END]" in ctx.provider.last_messages[1].content
    # 不重复注入(第二轮)(§149)
    ctx.provider.replies = [ChatResult(text="第二轮回答", model="m")]
    r2 = s.ask("继续")
    assert r2.status == "completed"
    pack_count = sum(1 for m in ctx.provider.last_messages
                     if m.role == "user" and "[PROJECT_DATA_BEGIN]" in m.content)
    assert pack_count == 1


def test_fallback_context_contains_project_facts(tmp_path):
    from core.chapter import confirm_draft, write_draft
    ctx = make_context(tmp_path)
    write_draft(ctx.project, 1, "t", "c")
    confirm_draft(ctx.project, 1)
    write_draft(ctx.project, 2, "t2", "c2")
    confirm_draft(ctx.project, 2)
    write_rel(ctx, "outline/summary.md", "第一卷共 10 章")
    write_rel(ctx, "outline/volumes/vol001.md", "# 第一卷\n- ch0001\n- ch0002")
    pack = build_fallback_context(ctx)
    assert '"current_chapter": 2' in pack
    assert "第一卷共 10 章" in pack
    assert "FACT_SOURCE" in pack
    assert "[PROJECT_DATA_BEGIN]" in pack


def test_fallback_context_no_full_chapter_dump(tmp_path):
    """chapters/ 正文绝不进入 fallback pack(§82, 139)。"""
    ctx = make_context(tmp_path)
    write_rel(ctx, "chapters/ch0001.md", "正文 DO_NOT_AUTO_INCLUDE_12345")
    pack = build_fallback_context(ctx)
    assert "DO_NOT_AUTO_INCLUDE_12345" not in pack


def test_fallback_context_bounded(tmp_path):
    """大量资料 → pack 不超硬上限(§83, 138)。"""
    ctx = make_context(tmp_path)
    write_rel(ctx, "outline/summary.md", "梗概 " + "长" * 50000)
    write_rel(ctx, "outline/volumes/vol001.md", "卷大纲 " + "长" * 50000)
    write_rel(ctx, "memory/index.md", "索引 " + "长" * 50000)
    pack = build_fallback_context(ctx)
    from agents.context import FALLBACK_BUDGET_CHARS
    assert len(pack) <= FALLBACK_BUDGET_CHARS + 512  # 头部/尾部标记少量开销


# ── session 两轮 / 裁剪(§89-92, 149-150) ─────────────────

def test_session_second_ask_keeps_history(tmp_path):
    ctx = make_context(tmp_path)
    s = make_session(ctx, [ChatResult(text="第一轮回复", model="m")])
    r1 = s.ask("第一问")
    assert r1.status == "completed"
    ctx.provider.replies = [ChatResult(text="第二轮回复", model="m")]
    r2 = s.ask("第二问")
    assert r2.status == "completed"
    # 第二轮请求包含第一轮对话(system + 前一轮 user/assistant)
    msgs = ctx.provider.last_messages
    roles = [m.role for m in msgs]
    assert roles.count("system") == 1  # system 不重复(§149)
    assert roles[0] == "system"
    contents = [m.content for m in msgs]
    assert "第一问" in contents and "第一轮回复" in contents
    assert "第二问" in contents


def test_session_trim_old_messages(tmp_path):
    ctx = make_context(tmp_path)
    s = AgentSession(ctx)
    # 注入大量消息触发裁剪(§92: 保留开头 + 最近; system 动态注入不受影响)
    for i in range(SESSION_MAX_MESSAGES + 20):
        s.messages.append(ChatMessage(role="user", content=f"历史消息{i}"))
    ctx.provider.replies = [ChatResult(text="done", model="m")]
    r = s.ask("最新问题")
    assert r.status == "completed"
    assert len(s.messages) <= SESSION_MAX_MESSAGES
    # 最新用户问题保留(最后一条是模型回复)
    contents = [m.content for m in s.messages]
    assert "最新问题" in contents
    assert s.messages[-1].role == "assistant" and s.messages[-1].content == "done"


# ── helper 兼容(旧 FakeProvider 字段) ────────────────────

def test_fake_provider_legacy_fields(tmp_path):
    ctx = make_context(tmp_path)
    ctx.provider.error = None
    ctx.provider.reply_text = "旧接口回复"
    s = AgentSession(ctx)
    r = s.ask("hi")
    assert r.status == "completed" and r.text == "旧接口回复"


def write_rel(ctx, rel: str, text: str) -> None:
    p = ctx.project.store.safe_path(ctx.project.id, rel)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
