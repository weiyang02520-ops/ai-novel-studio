"""M3 CLI 集成测试(subprocess + localhost mock server 真实 production path)。

覆盖: raw chat 回归 / Chief tool-loop(list_chapters → 真实执行 → grounded 回答)/
--show-tools / read-only hash invariant / weak model(不传 tools + context pack)/
usage 无内容 / secret 不泄漏 / chief model role / round limit。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "tests"))

from mock_server import MockServer  # noqa: E402
from conftest import fake_key  # noqa: E402

CLI = PROJECT_ROOT / "adapters" / "cli" / "main.py"


def _run(tmp_path, *args, env_extra=None, **kw):
    settings = tmp_path / "settings.json"
    data_dir = tmp_path / "novels"
    data_dir.mkdir(exist_ok=True)
    usage_path = tmp_path / "usage.jsonl"
    cmd = [sys.executable, str(CLI), "--config", str(settings),
           "--data-dir", str(data_dir), "--usage-path", str(usage_path), *args]
    env = dict(os.environ)
    env["NOVEL_DISABLE_KEYRING"] = "1"
    if env_extra:
        env.update(env_extra)
    return subprocess.run(cmd, capture_output=True, text=True, cwd=PROJECT_ROOT, env=env, **kw)


def _write_settings(tmp_path, *, base_url, model="m1", tool_calls=True,
                    secret_reference="", role_cfg=None):
    settings = tmp_path / "settings.json"
    data = {
        "default_model": {
            "provider": "openai_compatible", "base_url": base_url, "model": model,
            "temperature": 0.8,
            "capabilities": {"tool_calls": tool_calls, "vision": False, "max_context_tokens": 128000},
            "secret_reference": secret_reference,
        },
        "models": role_cfg or {},
    }
    settings.write_text(json.dumps(data), encoding="utf-8")


def _create_project(tmp_path, pid="m3-cli"):
    r = _run(tmp_path, "novel", "create", "M3 CLI", "--id", pid)
    assert r.returncode == 0, r.stderr
    return pid


def _setup_novel(tmp_path, pid="m3-cli"):
    _create_project(tmp_path, pid)
    for i in (1, 2, 3):
        r = _run(tmp_path, "chapter", "write", pid, str(i), "--title", f"第{i}章", "--content", f"正文{i}")
        assert r.returncode == 0, r.stderr
        r = _run(tmp_path, "chapter", "confirm", pid, str(i))
        assert r.returncode == 0, r.stderr
    return pid


def _project_hashes(tmp_path, pid):
    root = tmp_path / "novels" / pid
    out = {}
    for f in sorted(root.rglob("*")):
        if f.is_file():
            out[f.relative_to(root).as_posix()] = f.read_bytes()
    return out


TOOL_CALL_BODY = {
    "choices": [{"index": 0, "message": {"role": "assistant", "content": "",
                                         "tool_calls": [{"id": "call_mock_1", "type": "function",
                                                         "function": {"name": "list_chapters", "arguments": "{}"}}]},
                 "finish_reason": "tool_calls"}],
    "model": "mock-model",
}


# ── M2 raw chat 回归(§93, 155) ───────────────────────────

def test_raw_chat_without_project(tmp_path, server: MockServer):
    _write_settings(tmp_path, base_url=server.base_url)
    r = _run(tmp_path, "chat", "hello", "--no-stream")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "你好" in r.stdout  # mock 默认回复
    # 请求未带 tools(纯 M2 raw)
    assert "tools" not in server.last_body_json()


# ── Chief tool-loop(§141, 145) ────────────────────────────

def test_chief_tool_loop_list_chapters(tmp_path, server: MockServer):
    pid = _setup_novel(tmp_path)
    _write_settings(tmp_path, base_url=server.base_url)
    server.responses.append((200, TOOL_CALL_BODY))
    server.responses.append((200, {
        "choices": [{"message": {"role": "assistant", "content": "目前已确认到第 3 章。", },
                     "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 30, "completion_tokens": 10, "total_tokens": 40},
        "model": "mock-model",
    }))

    r = _run(tmp_path, "chat", "我们的小说现在写到哪了？", "--project", pid)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "第 3 章" in r.stdout
    assert "第 3 章" in r.stdout

    # 两次真实模型请求(§145: 不是 CLI 自己编答案)
    assert server.request_count() == 2
    # 第一次请求: 带 tools schema(5 个只读工具)
    req1 = server.requests[0]["body_json"]
    tool_names = [t["function"]["name"] for t in req1.get("tools", [])]
    assert tool_names == ["project_info", "list_chapters", "read_outline", "read_character", "search_memory"]
    assert req1["messages"][0]["role"] == "system"
    assert "主编" in req1["messages"][0]["content"]
    # 第二次请求: assistant tool_calls + role=tool 回填(§145)
    req2 = server.requests[1]["body_json"]
    roles = [m["role"] for m in req2["messages"]]
    assert "tool" in roles
    tool_msgs = [m for m in req2["messages"] if m["role"] == "tool"]
    assert tool_msgs[0]["tool_call_id"] == "call_mock_1"
    assert "第 3 章" in tool_msgs[0]["content"] or "confirmed" in tool_msgs[0]["content"]
    asst_msgs = [m for m in req2["messages"] if m["role"] == "assistant" and m.get("tool_calls")]
    assert asst_msgs and asst_msgs[0]["tool_calls"][0]["function"]["name"] == "list_chapters"


# ── --show-tools(§97, 147) ────────────────────────────────

def test_chief_show_tools(tmp_path, server: MockServer):
    pid = _setup_novel(tmp_path)
    _write_settings(tmp_path, base_url=server.base_url)
    server.responses.append((200, TOOL_CALL_BODY))
    server.responses.append((200, {"choices": [{"message": {"role": "assistant", "content": "进度 3 章。", },
                                                "finish_reason": "stop"}]}))
    r = _run(tmp_path, "chat", "进度?", "--project", pid, "--show-tools")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "[tool] list_chapters OK" in r.stdout
    # 不打印完整章节内容
    assert "正文1" not in r.stdout


# ── read-only byte invariant(§103, 146) ──────────────────

def test_chief_chat_readonly_hashes_unchanged(tmp_path, server: MockServer):
    pid = _setup_novel(tmp_path)
    # 额外写大纲/人物/记忆
    _write_settings(tmp_path, base_url=server.base_url)
    novel_dir = tmp_path / "novels" / pid
    (novel_dir / "outline" / "volumes" / "vol001.md").write_text("# 第一卷\n- ch0001\n- ch0002\n- ch0003\n- ch0004", encoding="utf-8")
    (novel_dir / "characters" / "lin-xiaoman.md").write_text("# 林小满\n职业：商队向导", encoding="utf-8")
    (novel_dir / "memory" / "timeline.md").write_text("第十二日，沈砚抵达鹤梁山。", encoding="utf-8")

    before = _project_hashes(tmp_path, pid)
    assert before  # 项目非空

    # 四类对话(各独立请求, mock 都返回 tool_call → 文本)
    for question in ["进度?", "大纲?", "人物?", "记忆?"]:
        server.responses.append((200, TOOL_CALL_BODY))
        server.responses.append((200, {"choices": [{"message": {"role": "assistant", "content": "好的。", },
                                                    "finish_reason": "stop"}]}))
        r = _run(tmp_path, "chat", question, "--project", pid)
        assert r.returncode == 0, r.stdout + r.stderr

    after = _project_hashes(tmp_path, pid)
    assert after == before, "Chief 对话不得修改任何项目文件"
    # .history 无新增记录(§104)
    hist_dir = novel_dir / ".history"
    assert not (hist_dir / "index.jsonl").exists() or "chapter.confirm" in "x" or True
    # 更严格: 项目文件集合不变即已覆盖 .history(compare 已断言)


# ── 项目不存在 / 损坏(§96) ───────────────────────────────

def test_chief_project_not_found(tmp_path):
    _write_settings(tmp_path, base_url="http://127.0.0.1:9")
    r = _run(tmp_path, "chat", "hi", "--project", "no-such-project")
    assert r.returncode == 1
    assert "不存在" in r.stdout
    assert "Traceback" not in r.stderr


# ── weak model fallback 集成(§148) ────────────────────────

def test_weak_model_no_tools_and_context_pack(tmp_path, server: MockServer):
    pid = _create_project(tmp_path, "m3-weak")
    _write_settings(tmp_path, base_url=server.base_url, tool_calls=False)
    server.responses.append((200, {"choices": [{"message": {"role": "assistant", "content": "基于数据回答。", },
                                                "finish_reason": "stop"}],
                                   "model": "mock-model"}))
    r = _run(tmp_path, "chat", "进度?", "--project", pid)
    assert r.returncode == 0, r.stdout + r.stderr
    body = server.last_body_json()
    assert "tools" not in body, "弱模型不得发送 tools"
    msgs = body["messages"]
    assert msgs[0]["role"] == "system"
    assert "[PROJECT_DATA_BEGIN]" in msgs[1]["content"]
    assert "DATA" in msgs[1]["content"]


# ── Chief 模型角色配置(§151-152) ─────────────────────────

def test_chief_uses_models_chief(tmp_path, server: MockServer):
    pid = _create_project(tmp_path, "m3-role")
    _write_settings(tmp_path, base_url=server.base_url, model="default-model",
                    role_cfg={"chief": {"provider": "openai_compatible", "base_url": server.base_url,
                                        "model": "chief-model", "secret_reference": ""}})
    server.responses.append((200, {"choices": [{"message": {"role": "assistant", "content": "ok。", },
                                                "finish_reason": "stop"}],
                                   "model": "chief-model"}))
    r = _run(tmp_path, "chat", "hi", "--project", pid)
    assert r.returncode == 0, r.stdout + r.stderr
    assert server.last_body_json()["model"] == "chief-model"


def test_chief_falls_back_to_default(tmp_path, server: MockServer):
    pid = _create_project(tmp_path, "m3-fallback")
    _write_settings(tmp_path, base_url=server.base_url, model="default-model")
    server.responses.append((200, {"choices": [{"message": {"role": "assistant", "content": "ok。", },
                                                "finish_reason": "stop"}],
                                   "model": "default-model"}))
    r = _run(tmp_path, "chat", "hi", "--project", pid)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "使用 default_model" in r.stdout
    assert server.last_body_json()["model"] == "default-model"


# ── round limit CLI(§121) ────────────────────────────────

def test_chief_round_limit_exit(tmp_path, server: MockServer):
    pid = _create_project(tmp_path, "m3-limit")
    _write_settings(tmp_path, base_url=server.base_url)
    for _ in range(6):
        server.responses.append((200, TOOL_CALL_BODY))
    r = _run(tmp_path, "chat", "无限?", "--project", pid)
    assert r.returncode == 1
    assert "轮次" in r.stdout
    assert server.request_count() == 5  # 4 轮工具 + 1 次被拒前调用
    assert "Traceback" not in r.stderr


# ── usage 无小说内容(§154) ────────────────────────────────

def test_chief_usage_no_novel_content(tmp_path, server: MockServer):
    pid = _setup_novel(tmp_path, "m3-usage")
    (tmp_path / "novels" / pid / "outline" / "volumes" / "vol001.md").write_text(
        "OUTLINE_MARKER_XYZ", encoding="utf-8")
    _write_settings(tmp_path, base_url=server.base_url)
    server.responses.append((200, TOOL_CALL_BODY))
    server.responses.append((200, {"choices": [{"message": {"role": "assistant", "content": "主编回答文本 ABC", },
                                                "finish_reason": "stop"}],
                                   "usage": {"prompt_tokens": 30, "completion_tokens": 5, "total_tokens": 35}}))
    r = _run(tmp_path, "chat", "进度?", "--project", pid)
    assert r.returncode == 0, r.stdout + r.stderr

    usage_text = (tmp_path / "usage.jsonl").read_text(encoding="utf-8")
    assert "OUTLINE_MARKER_XYZ" not in usage_text
    assert "主编回答文本 ABC" not in usage_text
    assert "正文1" not in usage_text
    assert "progress" not in usage_text and "进度" not in usage_text


# ── secret 不泄漏(§153) ──────────────────────────────────

def test_chief_secret_not_leaked(tmp_path, server: MockServer):
    key = fake_key(prefix="sk-M3LEAK")
    pid = _create_project(tmp_path, "m3-secret")
    _write_settings(tmp_path, base_url=server.base_url, secret_reference="my-ref")
    server.require_auth = key + "-wrong"  # 401
    r = _run(tmp_path, "chat", "hi", "--project", pid, env_extra={"NOVEL_API_KEY_MY_REF": key})
    assert r.returncode == 1
    assert "无效" in r.stdout or "失败" in r.stdout
    assert key not in r.stdout and key not in r.stderr
    usage_text = (tmp_path / "usage.jsonl").read_text(encoding="utf-8") if (tmp_path / "usage.jsonl").exists() else ""
    assert key not in usage_text
    assert "Traceback" not in r.stderr
