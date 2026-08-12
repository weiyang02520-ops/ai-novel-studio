"""M4 knowledge workspace, diagnostics, audit and undo CLI."""
from __future__ import annotations

import json

from core import history as history_core
from core import knowledge
from core.mutation import file_revision
from adapters.cli.commands import _open_project_or_error


def _files(p, root):
    return knowledge.safe_markdown_files(p, [root])


def cmd_outline(args):
    p = _open_project_or_error(args)
    if p is None: return 1
    if args.outline_command == "list":
        for path in _files(p, "outline"):
            print(f"{path.relative_to(p.dir).as_posix()}  {file_revision(path)[:12]}")
        return 0
    if args.outline_command == "status":
        s = knowledge.inspect_knowledge_status(p)
        print(f"summary: {s['outline_summary_exists']}")
        print(f"volume count: {len(_files(p, 'outline/volumes'))}")
        print(f"chapter-outline count: {len(_files(p, 'outline/chapters'))}")
        print(f"current volume exists: {s['current_volume_outline_exists']}")
        print(f"current chapter outline exists: {s['current_chapter_outline_exists']}")
        return 0
    rel = "outline/summary.md"
    if args.volume: rel = f"outline/volumes/vol{args.volume:03d}.md"
    if args.chapter: rel = f"outline/chapters/ch{args.chapter:04d}.md"
    path = p.store.safe_path(p.id, rel)
    if not path.is_file(): print(f"NOT_FOUND: {rel}"); return 1
    print(f"SOURCE: {rel}\nREVISION_SHA256: {file_revision(path)}\nCONTENT:")
    print(path.read_text(encoding="utf-8"), end="")
    return 0


def _named(args, root):
    p = _open_project_or_error(args)
    if p is None: return 1
    files = _files(p, root)
    if args.action == "list":
        for path in files:
            text = path.read_text(encoding="utf-8")
            print(f"{path.stem}\t{knowledge.first_h1(text)}\t{path.stat().st_size}\t{file_revision(path)[:12]}")
        return 0
    if args.action == "search":
        key = args.keyword.casefold()
        for path in files:
            text = path.read_text(encoding="utf-8")
            if key in text.casefold(): print(f"{path.stem}\t{knowledge.first_h1(text)}")
        return 0
    matches = [x for x in files if x.stem == args.name or knowledge.first_h1(x.read_text(encoding="utf-8")) == args.name]
    if len(matches) != 1:
        print("AMBIGUOUS" if len(matches) > 1 else "NOT_FOUND")
        return 1
    path = matches[0]
    print(f"SOURCE: {path.relative_to(p.dir).as_posix()}\nREVISION_SHA256: {file_revision(path)}\nCONTENT:")
    print(path.read_text(encoding="utf-8"), end="")
    return 0


def cmd_character(args): return _named(args, "characters")
def cmd_world(args): return _named(args, "world")


def cmd_memory(args):
    p = _open_project_or_error(args)
    if p is None: return 1
    if args.memory_command == "search":
        for hit in knowledge.search_knowledge(p, args.keyword):
            if hit["relative_path"].startswith("memory/"):
                print(f"{hit['relative_path']}:{hit['line']} TYPE: {hit['type']} {hit['snippet']}")
        return 0
    rel = "memory/foreshadowing/index.md" if args.kind == "foreshadowing" else f"memory/{args.kind}.md"
    path = p.store.safe_path(p.id, rel)
    if not path.is_file(): print(f"NOT_FOUND: {rel}"); return 1
    print(f"SOURCE: {rel}\nTYPE: DERIVED_MEMORY\nREVISION_SHA256: {file_revision(path)}\nCONTENT:")
    print(path.read_text(encoding="utf-8"), end="")
    return 0


def cmd_rules(args):
    p = _open_project_or_error(args)
    if p is None: return 1
    path = p.store.safe_path(p.id, "rules/writing_rules.md")
    if not path.is_file(): print("NOT_FOUND"); return 1
    print(f"REVISION_SHA256: {file_revision(path)}\nCONTENT:")
    print(path.read_text(encoding="utf-8"), end="")
    return 0


def cmd_knowledge(args):
    p = _open_project_or_error(args)
    if p is None: return 1
    if args.knowledge_command == "search":
        for hit in knowledge.search_knowledge(p, args.keyword, include_chapters=args.include_chapters):
            print(f"{hit['relative_path']}:{hit['line']} TYPE: {hit['type']} {hit['snippet']}")
        return 0
    if args.knowledge_command == "doctor":
        issues = knowledge.doctor(p)
        if not issues: print("KNOWLEDGE DOCTOR PASS"); return 0
        for i in issues: print(f"{i['severity']} {i['code']}: {i['message']}")
        return 1 if any(i["severity"] == "ERROR" for i in issues) else 0
    for item in knowledge.knowledge_revisions(p):
        print(f"{item['relative_path']}\t{item['sha256'][:12]}\t{item['size']}")
    return 0


def cmd_history_show(args):
    p = _open_project_or_error(args)
    if p is None: return 1
    match = next((r for r in history_core.list_history(p) if r["seq"] == args.seq), None)
    if not match: print("NOT_FOUND"); return 1
    safe = {k: match.get(k) for k in ("seq", "operation", "timestamp", "changes", "metadata") if k in match}
    if "metadata" in safe and isinstance(safe["metadata"], dict):
        safe["metadata"] = dict(safe["metadata"])
        diff = safe["metadata"].get("diff")
        if isinstance(diff, dict): diff.pop("preview", None)
    print(json.dumps(safe, ensure_ascii=False, indent=2))
    return 0


def cmd_audit(args):
    p = _open_project_or_error(args)
    if p is None: return 1
    for r in history_core.list_history(p):
        if not str(r.get("operation", "")).startswith("ai."): continue
        target = (r.get("changes") or [{}])[0].get("target", "?")
        print(f"{r['seq']}\t{r['operation']}\t{target}\t{r['timestamp']}")
    return 0


def cmd_undo_alias(args):
    p = _open_project_or_error(args)
    if p is None: return 1
    try: rec = history_core.undo_last(p)
    except Exception as e: print(f"错误: {e}"); return 1
    print(f"UNDO OK\noperation: {rec['operation']}\ntargets: {', '.join(c['target'] for c in rec['changes'])}")
    return 0

