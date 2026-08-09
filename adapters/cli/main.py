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

from core.config import Settings, validate_settings  # noqa: E402
from llm.secret_store import default_secret_store  # noqa: E402

DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "settings.json"


def cmd_config_validate(args: argparse.Namespace) -> int:
    s = Settings.load(args.config_path)
    store = default_secret_store()
    issues = validate_settings(s, store)
    print(f"配置: {args.config_path}")
    if not issues:
        print("✓ 配置有效(本地校验通过, 未联网)")
        return 0
    for i in issues:
        print(f"  {i}")
    errors = [i for i in issues if i.severity == "error"]
    print(f"共 {len(issues)} 项(错误 {len(errors)} 项)")
    return 1 if errors else 0


def cmd_config_show(args: argparse.Namespace) -> int:
    s = Settings.load(args.config_path)
    print("default_model:")
    print(f"  provider: {s.default_model.provider}")
    print(f"  base_url: {s.default_model.base_url}")
    print(f"  model: {s.default_model.model}")
    print(f"  temperature: {s.default_model.temperature}")
    print(f"  capabilities: tool_calls={s.default_model.tool_calls}, "
          f"vision={s.default_model.vision}, max_context_tokens={s.default_model.max_context_tokens}")
    print(f"  secret_reference: {s.default_model.secret_reference}")
    for role, cfg in s.models.items():
        print(f"models.{role}: provider={cfg.provider}, base_url={cfg.base_url}, model={cfg.model}")
    print("(API Key 不显示; 存储于 SecretStore)")


def cmd_config_set(args: argparse.Namespace) -> int:
    s = Settings.load(args.config_path)
    try:
        s.set_value(args.key, args.value)
        print(f"已设置 {args.key} = {args.value}")
    except (KeyError, TypeError, ValueError) as e:
        print(f"设置失败: {e}")
        return 1
    return 0


def cmd_config_set_key(args: argparse.Namespace) -> int:
    """交互式输入 Key(不回显), 存入 SecretStore。"""
    try:
        import getpass
    except Exception:
        getpass = None
    store = default_secret_store()
    try:
        if getpass:
            value = getpass.getpass(f"API Key for '{args.reference}': ")
        else:
            value = input(f"API Key for '{args.reference}': ")
    except (EOFError, KeyboardInterrupt):
        print("\n已取消")
        return 1
    if not value.strip():
        print("未输入内容, 取消")
        return 1
    store.set(args.reference, value.strip())
    print(f"✓ 已存入 SecretStore(reference={args.reference}); 未写入任何文件/日志")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="ai-novel-studio",
        description="个人版 AI 小说创作软件(M0)",
    )
    p.add_argument("--config", dest="config_path", type=Path, default=DEFAULT_CONFIG_PATH,
                   help="settings.json 路径")
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

    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if not args.command:
        build_parser().print_help()
        return 0

    if args.command == "config":
        if not getattr(args, "config_command", None):
            print("用法: ai-novel-studio config {validate|show|set|set-key}")
            return 0
        if args.config_command == "validate":
            return cmd_config_validate(args)
        if args.config_command == "show":
            return cmd_config_show(args)
        if args.config_command == "set":
            return cmd_config_set(args)
        if args.config_command == "set-key":
            return cmd_config_set_key(args)

    print(f"未知命令: {args.command}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
