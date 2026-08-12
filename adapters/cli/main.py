"""CLI Adapter — M0-M7 测试入口。

只有这一层允许使用 argparse / 终端 IO。
Core Engine(core/ agents/ llm/ tools/) 不依赖本层。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from core.config import ConfigError, Settings, validate_settings  # noqa: E402
from llm.secret_store import SecretStoreError, default_secret_store  # noqa: E402

DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "settings.json"


def cmd_config_validate(args: argparse.Namespace) -> int:
    try:
        s = Settings.load(args.config_path)
    except ConfigError as e:
        print(e)
        return 1
    store = default_secret_store()
    try:
        issues = validate_settings(s, store)
    except SecretStoreError as e:
        print(f"配置: {args.config_path}")
        print(f"  [ERROR] SecretStore 不可用: {e}")
        return 1
    print(f"配置: {args.config_path}")
    errors = [i for i in issues if i.severity == "error"]
    if not errors:
        print("✓ 配置有效(本地校验通过, 未联网)")
        for i in issues:  # warning(如 keyless)仍需展示
            print(f"  {i}")
        return 0
    for i in issues:
        print(f"  {i}")
    print(f"共 {len(issues)} 项(错误 {len(errors)} 项); 本地校验, 未联网")
    return 1


def cmd_config_show(args: argparse.Namespace) -> int:
    try:
        s = Settings.load(args.config_path)
    except ConfigError as e:
        print(e)
        return 1

    def key_status(ref: str) -> str:
        """Key 存在性(configured/missing/unknown)。绝不显示 Key 内容/长度。"""
        if not ref:
            return "n/a"
        store = default_secret_store()
        try:
            return "configured" if store.exists(ref) else "missing"
        except SecretStoreError as e:
            return "unknown" if e.code == "BACKEND_UNAVAILABLE" else f"unknown({e.code})"

    def show_model(label: str, cfg) -> None:
        print(f"{label}:")
        print(f"  provider: {cfg.provider}")
        print(f"  base_url: {cfg.base_url}")
        print(f"  model: {cfg.model}")
        print(f"  temperature: {cfg.temperature}")
        print(f"  capabilities: tool_calls={cfg.tool_calls}, "
              f"vision={cfg.vision}, max_context_tokens={cfg.max_context_tokens}")
        print(f"  secret_reference: {cfg.secret_reference}")
        print(f"  key: {key_status(cfg.secret_reference)}")

    show_model("default_model", s.default_model)
    for role, cfg in s.models.items():
        show_model(f"models.{role}", cfg)
    print("(API Key 内容/长度/前后缀不显示; 存储于 SecretStore)")
    return 0


def cmd_config_set(args: argparse.Namespace) -> int:
    try:
        s = Settings.load(args.config_path)
        s.set_value(args.key, args.value)
        print(f"已设置 {args.key} = {args.value}")
        return 0
    except ConfigError as e:
        print(e)
        return 1


def cmd_config_set_key(args: argparse.Namespace) -> int:
    """交互式输入 Key(不回显), 存入 SecretStore。

    安全规则:
    - TTY 环境: 只能使用 getpass(隐藏输入); getpass 不可用/异常 → 明确报错 exit 1,
      绝不 fallback input()(否则 Key 会显示在屏幕上)。
    - 非 TTY(pipe/CI/自动化): 允许从 stdin 读取; 但 stdout/stderr/异常 绝不包含 Key。
    """
    import sys as _sys
    store = default_secret_store()
    value: str | None = None

    if _sys.stdin.isatty():
        # ── TTY: 只允许隐藏输入 ──
        try:
            import getpass
            value = getpass.getpass(f"API Key for '{args.reference}': ")
        except (EOFError, KeyboardInterrupt):
            print("\n已取消")
            return 1
        except Exception:
            print("无法使用隐藏输入(getpass 不可用)。为安全起见已中止, 请配置终端后重试。")
            return 1
    else:
        # ── 非 TTY(pipe/CI/自动化): 从 stdin 读取, 不回显 ──
        try:
            value = input(f"API Key for '{args.reference}': ")
        except (EOFError, KeyboardInterrupt):
            print("\n已取消")
            return 1

    if value is None or not value.strip():
        print("未输入内容, 取消")
        return 1
    try:
        store.set(args.reference, value.strip())
    except SecretStoreError as e:
        print(f"设置密钥失败: {e}")
        return 1
    print(f"✓ 已存入 SecretStore(reference={args.reference}); 未写入任何文件/日志")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="ai-novel-studio",
        description="个人版 AI 小说创作软件",
    )
    p.add_argument("--config", dest="config_path", type=Path, default=DEFAULT_CONFIG_PATH,
                   help="settings.json 路径")
    p.add_argument("--data-dir", dest="data_dir", type=Path, default=None,
                   help="小说数据目录(默认 data/novels/, 测试用临时目录)")
    p.add_argument("--usage-path", dest="usage_path", type=Path,
                   default=PROJECT_ROOT / "data" / "logs" / "usage.jsonl",
                   help="usage 记录文件(默认 data/logs/usage.jsonl, 测试用临时路径)")
    sub = p.add_subparsers(dest="command")

    cfg = sub.add_parser("config", help="配置管理")
    cfg_sub = cfg.add_subparsers(dest="config_command")

    cfg_sub.add_parser("validate", help="本地校验配置(不联网)")
    cfg_sub.add_parser("show", help="显示配置(不含 Key)")
    setp = cfg_sub.add_parser("set", help="设置配置项, 如 default_model.base_url")
    setp.add_argument("key")
    setp.add_argument("value")
    setp2 = cfg_sub.add_parser("set-key", help="交互式设置 API Key 到 SecretStore(不回显)")
    setp2.add_argument("reference", help="secret_reference 名称, 如 deepseek-main")
    tprov = cfg_sub.add_parser("test-provider", help="真实联网测试 Provider 连接(chat/completions)")
    tprov.add_argument("--role", help="模型配置 profile(writer/reviewer...); 缺省 default_model")
    dk = cfg_sub.add_parser("delete-key", help="从 SecretStore 删除 Key(不显示旧值)")
    dk.add_argument("reference")
    ks = cfg_sub.add_parser("key-status", help="查询 Key 是否已配置(不显示 Key)")
    ks.add_argument("reference")

    # ── chat ──
    chat = sub.add_parser("chat", help="直接对话(无 --project = raw Provider; 有 --project = 主编项目对话)")
    chat.add_argument("prompt", help="用户消息")
    chat.add_argument("--role", help="模型配置 profile(writer/reviewer...); 缺省 default_model")
    chat.add_argument("--system", help="system 消息(可选; 仅 raw chat)")
    chat.add_argument("--no-stream", action="store_true", help="非流式: 完整响应后一次打印")
    chat.add_argument("--temperature", type=float, default=None, help="覆盖本次请求温度(不写入 settings.json)")
    chat.add_argument("--project", help="项目 ID: 走 Chief 主编(只读工具 + grounded 回答)")
    chat.add_argument("--show-tools", action="store_true", help="主编对话: 显示工具调用 trace(不含工具内容)")

    # ── usage ──
    usage = sub.add_parser("usage", help="本地 usage 统计")
    usage_sub = usage.add_subparsers(dest="usage_command")
    usage_sub.add_parser("summary", help="汇总统计")
    urec = usage_sub.add_parser("recent", help="最近记录")
    urec.add_argument("--limit", type=int, default=10, help="条数(默认 10)")

    # ── novel ──
    novel = sub.add_parser("novel", help="小说项目管理")
    novel_sub = novel.add_subparsers(dest="novel_command")
    ncreate = novel_sub.add_parser("create", help="创建小说")
    ncreate.add_argument("name", help="书名(可中文)")
    ncreate.add_argument("--id", dest="id", help="显式 project_id([a-z][a-z0-9_-]{1,63})")
    ncreate.add_argument("--genre", help="题材")
    novel_sub.add_parser("list", help="列出全部小说")
    nshow = novel_sub.add_parser("show", help="显示小说元数据")
    nshow.add_argument("project_id")
    nopen = novel_sub.add_parser("open", help="打开并验证项目(输出摘要)")
    nopen.add_argument("project_id")
    nval = novel_sub.add_parser("validate", help="项目一致性验证")
    nval.add_argument("project_id")

    # ── chapter ──
    chapter = sub.add_parser("chapter", help="章节管理")
    chapter_sub = chapter.add_subparsers(dest="chapter_command")
    cwrite = chapter_sub.add_parser("write", help="创建草稿")
    cwrite.add_argument("project_id")
    cwrite.add_argument("chapter", type=int)
    cwrite.add_argument("--title", default="")
    cwrite.add_argument("--content", default=None)
    cwrite.add_argument("--from-file")
    clist = chapter_sub.add_parser("list", help="列出章节")
    clist.add_argument("project_id")
    cread = chapter_sub.add_parser("read", help="读取章节")
    cread.add_argument("project_id")
    cread.add_argument("chapter", type=int)
    cread.add_argument("--draft", action="store_true", help="读草稿(默认读已确认)")
    cupd = chapter_sub.add_parser("update", help="更新草稿")
    cupd.add_argument("project_id")
    cupd.add_argument("chapter", type=int)
    cupd.add_argument("--title")
    cupd.add_argument("--content")
    cupd.add_argument("--from-file")
    cconf = chapter_sub.add_parser("confirm", help="手动确认草稿(收编到 chapters/)")
    cconf.add_argument("project_id")
    cconf.add_argument("chapter", type=int)

    # ── history ──
    hist = sub.add_parser("history", help="历史快照与回滚")
    hist_sub = hist.add_subparsers(dest="history_command")
    hund = hist_sub.add_parser("undo-last", help="回滚最近一次快照")
    hund.add_argument("project_id")
    hlist = hist_sub.add_parser("list", help="列出历史记录")
    hlist.add_argument("project_id")

    return p


def main(argv: list[str] | None = None) -> int:
    try:
        return _main(argv)
    except KeyboardInterrupt:
        # §63: 任何命令中的 Ctrl+C → 友好取消, 无 traceback
        print("\n已取消", file=sys.stderr)
        return 130


def _main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if not args.command:
        build_parser().print_help()
        return 0

    if args.command == "config":
        if not getattr(args, "config_command", None):
            print("用法: ai-novel-studio config {validate|show|set|set-key|test-provider|delete-key|key-status}")
            return 0
        if args.config_command == "validate":
            return cmd_config_validate(args)
        if args.config_command == "show":
            return cmd_config_show(args)
        if args.config_command == "set":
            return cmd_config_set(args)
        if args.config_command == "set-key":
            return cmd_config_set_key(args)
        # M2 命令(config test-provider / delete-key / key-status) — 懒加载
        import adapters.cli.m2 as m2
        if args.config_command == "test-provider":
            return m2.cmd_config_test_provider(args)
        if args.config_command == "delete-key":
            return m2.cmd_config_delete_key(args)
        if args.config_command == "key-status":
            return m2.cmd_config_key_status(args)

    # M2/M3 命令(chat / usage) — 懒加载
    import adapters.cli.m2 as m2

    if args.command == "chat":
        # §94: --project 存在 → Chief Agent; 否则 M2 raw provider chat(§93)
        if getattr(args, "project", None):
            import adapters.cli.m3 as m3
            return m3.cmd_chat_chief(args)
        return m2.cmd_chat(args)

    if args.command == "usage":
        if not getattr(args, "usage_command", None):
            print("用法: ai-novel-studio usage {summary|recent}")
            return 0
        if args.usage_command == "summary":
            return m2.cmd_usage_summary(args)
        if args.usage_command == "recent":
            return m2.cmd_usage_recent(args)

    # M1 命令(novel / chapter / history) — 懒加载避免 import 开销
    import adapters.cli.commands as m1

    if args.command == "novel":
        if not getattr(args, "novel_command", None):
            print("用法: ai-novel-studio novel {create|list|show|open|validate}")
            return 0
        if args.novel_command == "create":
            return m1.cmd_novel_create(args)
        if args.novel_command == "list":
            return m1.cmd_novel_list(args)
        if args.novel_command == "show":
            return m1.cmd_novel_show(args)
        if args.novel_command == "open":
            return m1.cmd_novel_open(args)
        if args.novel_command == "validate":
            return m1.cmd_novel_validate(args)

    if args.command == "chapter":
        if not getattr(args, "chapter_command", None):
            print("用法: ai-novel-studio chapter {write|list|read|update|confirm}")
            return 0
        if args.chapter_command == "write":
            return m1.cmd_chapter_write(args)
        if args.chapter_command == "list":
            return m1.cmd_chapter_list(args)
        if args.chapter_command == "read":
            return m1.cmd_chapter_read(args)
        if args.chapter_command == "update":
            return m1.cmd_chapter_update(args)
        if args.chapter_command == "confirm":
            return m1.cmd_chapter_confirm(args)

    if args.command == "history":
        if not getattr(args, "history_command", None):
            print("用法: ai-novel-studio history {undo-last|list}")
            return 0
        if args.history_command == "undo-last":
            return m1.cmd_history_undo_last(args)
        if args.history_command == "list":
            return m1.cmd_history_list(args)

    print(f"未知命令: {args.command}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
