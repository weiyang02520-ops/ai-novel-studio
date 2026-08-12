"""M3 CLI — Chief 项目级 grounded chat。

chat "..." --project <id> → Chief Agent(tool-call loop, 只读)。
chat "..."(无 --project)→ M2 raw provider chat(路由在 main.py)。
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from agents.definitions import m4_chief_agent_def  # noqa: E402
from agents.runtime import AgentSession  # noqa: E402
from agents.types import AgentContext  # noqa: E402
from core import project as project_core  # noqa: E402
from core.config import ConfigError, Settings  # noqa: E402
from core.storage import DataIntegrityError, ProjectStore, StorageError  # noqa: E402
from llm.factory import create_provider  # noqa: E402
from llm.provider import ProviderError  # noqa: E402
from llm.secret_store import default_secret_store  # noqa: E402
from llm.usage import UsageService  # noqa: E402
from tools.read_tools import build_chief_registry  # noqa: E402


def cmd_chat_chief(args) -> int:
    prompt = (args.prompt or "").strip()
    if not prompt:
        print("错误: 消息不能为空")
        return 1

    # 打开项目(§96: 必须用现有 ProjectStore/open_project; 损坏明确报错)
    try:
        data_dir = getattr(args, "data_dir", None) or PROJECT_ROOT / "data" / "novels"
        store = ProjectStore(Path(data_dir))
        proj = project_core.open_project(store, args.project)
    except (StorageError, DataIntegrityError) as e:
        print(f"错误: {e}")
        return 1

    try:
        s = Settings.load(args.config_path)
    except ConfigError as e:
        print(e)
        return 1

    # Chief 模型(§10, 98): models.chief 存在则用, 否则 default_model
    cfg = s.model_for("chief")
    if "chief" not in s.models:
        print("(主编模型未单独配置, 使用 default_model)")

    secret_store = default_secret_store()
    try:
        provider = create_provider(cfg, secret_store)
    except ProviderError as e:
        print(f"错误: {e.message}")
        return 1

    # Every project chat uses the complete M4 capability schema. Intent is a
    # model/prompt decision; the CLI never guesses it from keywords.
    agent = m4_chief_agent_def()
    registry = build_chief_registry()
    ctx = AgentContext(
        project=proj,
        settings=s,
        provider=provider,
        tool_registry=registry,
        agent_def=agent,
        max_tool_calls=int(s.workflow.get("max_tool_calls_per_turn", 8) or 8),
    )
    session = AgentSession(ctx)

    usage_svc = UsageService(getattr(args, "usage_path", None) or PROJECT_ROOT / "data" / "logs" / "usage.jsonl")
    started = time.monotonic()
    try:
        result = session.ask(prompt)
    finally:
        provider.close()  # §110: 所有路径(成功/ProviderError/工具错误/Ctrl+C)都关闭

    # usage 记录(§105-107): 每次 LLM call; 只记 metadata(§108)
    if result.calls:
        write_ok = True
        for c in result.calls:
            u = c.usage
            ok = usage_svc.record_success(
                provider=cfg.provider,
                model=c.model or cfg.model,
                prompt_tokens=u.prompt_tokens if u else 0,
                completion_tokens=u.completion_tokens if u else 0,
                total_tokens=u.total_tokens if u else 0,
                estimated=bool(u and u.estimated),
                duration_ms=c.duration_ms or ((time.monotonic() - started) * 1000),
                stream=False)
            write_ok = write_ok and ok
        if not write_ok:
            print("(warning: usage 记录写入失败, 不影响本次结果)", file=sys.stderr)

    # 输出(§101): 成功只输出最终回答; --show-tools 增加 trace(§97)
    if getattr(args, "show_tools", False):
        for t in result.tool_trace:
            if t.success:
                if t.mutates_project:
                    suffix = f" (+{t.added_lines or 0}/-{t.removed_lines or 0})"
                    print(f"[tool] {t.name} WRITE OK{suffix}")
                    if getattr(args, "show_diff", False) and t.diff_preview:
                        print(t.diff_preview)
                else:
                    print(f"[tool] {t.name} OK")
            else:
                print(f"[tool] {t.name} {t.error or 'FAILED'}")
        if result.tool_trace:
            print(f"[tool] rounds={result.rounds} calls={result.tool_calls_count}")

    if result.status == "completed":
        print(result.text)
        return 0
    if result.status == "provider_error":
        print(f"错误: {result.error_message or 'Provider 错误'}")
        return 1
    if result.status == "tool_limit_exceeded":
        print("主编工具调用次数达到上限, 已停止(本次未执行超限工具)。")
        if result.text:
            print(result.text)
        return 1
    if result.status == "round_limit_exceeded":
        print("主编工具轮次达到上限, 已停止。")
        if result.text:
            print(result.text)
        return 1
    print(f"(未完成: {result.status})")
    return 1
