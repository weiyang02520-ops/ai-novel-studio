"""M1 CLI 命令(novel / chapter / history)。

只负责: 参数解析 → 调用 Core → 展示结果。
Core 异常 → 人类可读错误 + exit 1, 无 traceback。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from core import chapter as chapter_core  # noqa: E402
from core import history as history_core  # noqa: E402
from core import project as project_core  # noqa: E402
from core.storage import DataIntegrityError, ProjectStore, StorageError  # noqa: E402

DEFAULT_DATA_DIR = PROJECT_ROOT / "data" / "novels"


def _store(args: argparse.Namespace) -> ProjectStore:
    data_dir = getattr(args, "data_dir", None) or DEFAULT_DATA_DIR
    return ProjectStore(Path(data_dir))


def _print_error(e: Exception) -> int:
    print(f"错误: {e}")
    return 1


# ── novel ────────────────────────────────────────────────

def cmd_novel_create(args: argparse.Namespace) -> int:
    try:
        p = project_core.create_project(_store(args), args.name, args.id, args.genre or "")
    except (StorageError, DataIntegrityError) as e:
        return _print_error(e)
    print(f"✓ 已创建小说: {p.name}(id={p.id})")
    print(f"  目录: {p.dir}")
    return 0


def cmd_novel_list(args: argparse.Namespace) -> int:
    projects = project_core.list_projects(_store(args))
    if not projects:
        print("(暂无小说)")
        return 0
    for p in projects:
        if p["valid"]:
            print(f"  {p['id']:24s} {p['name']:20s} {p['genre']:10s} "
                  f"status={p['status']}  ch={p['current_chapter']}")
        else:
            print(f"  {p['id']:24s} (INVALID) {p.get('error', '')}")
    return 0


def cmd_novel_show(args: argparse.Namespace) -> int:
    try:
        p = project_core.open_project(_store(args), args.project_id)
    except (StorageError, DataIntegrityError) as e:
        return _print_error(e)
    print(f"id:             {p.id}")
    print(f"name:           {p.name}")
    print(f"genre:          {p.genre}")
    print(f"status:         {p.status}")
    print(f"format_version: {p.metadata.get('format_version')}")
    print(f"current_volume: {p.metadata.get('current_volume')}")
    print(f"current_chapter:{p.current_chapter}")
    print(f"auto_accept:    {p.auto_accept}")
    print(f"created_at:     {p.metadata.get('created_at')}")
    print(f"updated_at:     {p.metadata.get('updated_at')}")
    print(f"目录:            {p.dir}")
    return 0


def cmd_novel_open(args: argparse.Namespace) -> int:
    """open = 从磁盘打开并验证 + 输出摘要(M1 不创建持久 session)。"""
    try:
        p = project_core.open_project(_store(args), args.project_id)
    except (StorageError, DataIntegrityError) as e:
        return _print_error(e)
    print(f"✓ 已打开: {p.name}(id={p.id})")
    print(f"  题材: {p.genre or '(未设置)'}  当前章节: {p.current_chapter}")
    chapters = chapter_core.list_chapters(p)
    print(f"  章节数: {len(chapters)}(draft {sum(1 for c in chapters if c['location']=='draft')} / "
          f"confirmed {sum(1 for c in chapters if c['location']=='confirmed')})")
    return 0


def cmd_novel_validate(args: argparse.Namespace) -> int:
    try:
        issues = project_core.validate_project(_store(args), args.project_id)
    except (StorageError, DataIntegrityError) as e:
        return _print_error(e)
    if not issues:
        print(f"✓ 项目 {args.project_id} 一致性验证通过")
        return 0
    print(f"项目 {args.project_id} 存在 {len(issues)} 个问题:")
    for i in issues:
        print(f"  - {i}")
    return 1


# ── chapter ──────────────────────────────────────────────

def _open_project_or_error(args) -> object | None:
    try:
        return project_core.open_project(_store(args), args.project_id)
    except (StorageError, DataIntegrityError) as e:
        _print_error(e)
        return None


def cmd_chapter_write(args: argparse.Namespace) -> int:
    p = _open_project_or_error(args)
    if p is None:
        return 1
    content = args.content or ""
    if args.from_file:
        try:
            content = Path(args.from_file).read_text(encoding="utf-8")
        except OSError as e:
            return _print_error(StorageError(f"无法读取文件 {args.from_file}: {e}"))
    try:
        c = chapter_core.write_draft(p, args.chapter, args.title or "", content)
    except (StorageError, DataIntegrityError) as e:
        return _print_error(e)
    print(f"✓ 已创建草稿 ch{c.number:04d}(title={c.title!r}, words={c.words})")
    return 0


def cmd_chapter_list(args: argparse.Namespace) -> int:
    p = _open_project_or_error(args)
    if p is None:
        return 1
    chapters = chapter_core.list_chapters(p)
    if not chapters:
        print("(暂无章节)")
        return 0
    print(f"{'章节':8s} {'标题':20s} {'状态':14s} {'字数':>6s}  更新时间")
    for c in chapters:
        loc = "草稿" if c["location"] == "draft" else "已确认"
        flag = "  [冲突!]" if c.get("conflict") else ""
        print(f"{c['chapter']:>6d} {c['title'][:18]:20s} {c['status']:14s} {c['words']:>6d}  {c['updated_at']}  [{loc}]{flag}")
    return 0


def cmd_chapter_read(args: argparse.Namespace) -> int:
    p = _open_project_or_error(args)
    if p is None:
        return 1
    try:
        if args.draft:
            c = chapter_core.read_draft(p, args.chapter)
        else:
            c = chapter_core.read_confirmed(p, args.chapter)
    except (StorageError, DataIntegrityError) as e:
        return _print_error(e)
    print(f"--- ch{c.number:04d} {c.title} [status={c.status}, origin={c.origin}, words={c.words}] ---")
    print(c.body, end="")
    if not c.body.endswith("\n"):
        print()
    return 0


def cmd_chapter_update(args: argparse.Namespace) -> int:
    p = _open_project_or_error(args)
    if p is None:
        return 1
    content = args.content
    if args.from_file:
        try:
            content = Path(args.from_file).read_text(encoding="utf-8")
        except OSError as e:
            return _print_error(StorageError(f"无法读取文件 {args.from_file}: {e}"))
    try:
        c = chapter_core.update_draft(p, args.chapter, title=args.title, content=content)
    except (StorageError, DataIntegrityError) as e:
        return _print_error(e)
    print(f"✓ 已更新草稿 ch{c.number:04d}(words={c.words})")
    return 0


def cmd_chapter_confirm(args: argparse.Namespace) -> int:
    p = _open_project_or_error(args)
    if p is None:
        return 1
    try:
        c = chapter_core.confirm_draft(p, args.chapter)
    except (StorageError, DataIntegrityError) as e:
        return _print_error(e)
    print(f"✓ 已确认章节 ch{c.number:04d}({c.title}) → chapters/{c.path.name}")
    print(f"  current_chapter = {p.current_chapter}")
    return 0


# ── history ──────────────────────────────────────────────

def cmd_history_undo_last(args: argparse.Namespace) -> int:
    p = _open_project_or_error(args)
    if p is None:
        return 1
    try:
        rec = history_core.undo_last(p)
    except (StorageError, DataIntegrityError) as e:
        return _print_error(e)
    targets = [ch.get("target", "?") for ch in rec.get("changes", [])]
    print(f"✓ 已回滚 [{rec.get('operation')}] targets={targets}(seq={rec.get('seq')})")
    return 0


def cmd_history_list(args: argparse.Namespace) -> int:
    p = _open_project_or_error(args)
    if p is None:
        return 1
    records = history_core.list_history(p)
    if not records:
        print("(无历史记录)")
        return 0
    for r in records:
        changes = r.get("changes") or []
        n = len(changes)
        if n == 0:
            targets = "(无目标)"
        elif n == 1:
            targets = changes[0].get("target", "?")
        else:
            targets = f"{changes[0].get('target', '?')}(+{n - 1})"
        print(f"  seq={r.get('seq'):>5d} {r.get('operation',''):20s} {targets:30s} {r.get('timestamp','')}")
    return 0
