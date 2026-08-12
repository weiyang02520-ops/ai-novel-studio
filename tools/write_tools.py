"""Chief knowledge mutations. All writes delegate to MutationService."""
from __future__ import annotations

import datetime
import hashlib
import re
from pathlib import Path
from typing import Any

from agents.types import AgentContext
from core.knowledge import first_h1, safe_markdown_files
from core.memory import MEMORY_KINDS, memory_target_for_kind
from core.mutation import ABSENT, MutationError, MutationRequest, MutationService
from tools.types import ToolDef, ToolExecutionError

SLUG_RE = re.compile(r"^[a-z][a-z0-9_-]{1,63}$")


def stable_slug(prefix: str, name: str) -> str:
    normalized = " ".join(name.strip().split()).casefold()
    return f"{prefix}-{hashlib.sha256(normalized.encode('utf-8')).hexdigest()[:8]}"


def _safe_slug(slug: str) -> str:
    if not SLUG_RE.fullmatch(slug or ""):
        raise ToolExecutionError("INVALID_SLUG: slug 必须匹配 [a-z][a-z0-9_-]{1,63}")
    return slug


def _resolve_named(ctx: AgentContext, root: str, name: str, slug: str | None,
                   prefix: str) -> tuple[str, bool]:
    name = name.strip()
    if not name:
        raise ToolExecutionError("INVALID_NAME: name 必填")
    if slug:
        chosen = _safe_slug(slug)
        path = ctx.project.store.safe_path(ctx.project.id, f"{root}/{chosen}.md")
        if path.is_file() and first_h1(path.read_text(encoding="utf-8")) not in ("", name):
            raise ToolExecutionError("SLUG_COLLISION: slug 已属于另一个条目")
        return chosen, path.is_file()
    matches = []
    for path in safe_markdown_files(ctx.project, [root]):
        text = path.read_text(encoding="utf-8")
        title = first_h1(text)
        if path.stem == name or title == name or title.startswith(name + "("):
            matches.append(path)
    if len(matches) > 1:
        raise ToolExecutionError(f"AMBIGUOUS: 多个文件匹配 {name!r}")
    if matches:
        return matches[0].stem, True
    ascii_slug = re.sub(r"[^a-z0-9]+", "-", name.casefold()).strip("-")
    chosen = ascii_slug if SLUG_RE.fullmatch(ascii_slug) else stable_slug(prefix, name)
    path = ctx.project.store.safe_path(ctx.project.id, f"{root}/{chosen}.md")
    if path.exists() and first_h1(path.read_text(encoding="utf-8")) != name:
        raise ToolExecutionError("SLUG_COLLISION: deterministic slug collision")
    return chosen, path.is_file()


def _format_result(result) -> str:
    if not result.changed:
        return f"NO_CHANGE\ntarget: {result.target}\nrevision: {result.old_revision}"
    d = result.diff
    return (f"WRITE_OK\ntarget: {result.target}\nrevision: {result.old_revision} -> {result.new_revision}\n"
            f"history_seq: {result.history_seq}\ndiff: +{d.added_lines}/-{d.removed_lines}\n"
            f"DIFF_PREVIEW_BEGIN\n{d.preview}\nDIFF_PREVIEW_END")


def _mutate(ctx: AgentContext, *, operation: str, rel: str, text: str,
            expected: str, kind: str) -> str:
    try:
        result = MutationService(ctx.project).mutate(MutationRequest(
            operation, rel, text, expected, kind, ctx.agent_def.id))
    except MutationError as e:
        raise ToolExecutionError(str(e)) from e
    return _format_result(result)


def _update_outline(ctx: AgentContext, args: dict[str, Any]) -> str:
    scope, number = args["scope"], args.get("number")
    if scope == "summary":
        if number is not None: raise ToolExecutionError("INVALID_NUMBER: summary 不接受 number")
        rel = "outline/summary.md"
    elif scope == "volume":
        if not isinstance(number, int) or number < 1: raise ToolExecutionError("INVALID_NUMBER: volume 需要正整数 number")
        rel = f"outline/volumes/vol{number:03d}.md"
    elif scope == "chapter":
        if not isinstance(number, int) or number < 1: raise ToolExecutionError("INVALID_NUMBER: chapter 需要正整数 number")
        rel = f"outline/chapters/ch{number:04d}.md"
    else:
        raise ToolExecutionError("INVALID_SCOPE: 仅 summary/volume/chapter")
    return _mutate(ctx, operation="ai.update_outline", rel=rel, text=args["text"],
                   expected=args["expected_revision"], kind="outline")


def _update_named(ctx: AgentContext, args: dict[str, Any], root: str, prefix: str, kind: str) -> str:
    slug, exists = _resolve_named(ctx, root, args["name"], args.get("slug"), prefix)
    if exists and args["expected_revision"] == ABSENT:
        raise ToolExecutionError("STALE_REVISION: 目标已存在")
    text = args["text"]
    if exists:
        path = ctx.project.store.safe_path(ctx.project.id, f"{root}/{slug}.md")
        current_h1, new_h1 = first_h1(path.read_text(encoding="utf-8")), first_h1(text)
        if not new_h1 or new_h1 != current_h1:
            raise ToolExecutionError(f"IDENTITY_MISMATCH: existing H1 {current_h1!r} 必须保持不变")
    elif not first_h1(text):
        text = f"# {args['name'].strip()}\n\n{text.lstrip()}"
    return _mutate(ctx, operation=f"ai.update_{kind}", rel=f"{root}/{slug}.md", text=text,
                   expected=args["expected_revision"], kind=kind)


def _update_character(ctx: AgentContext, args: dict[str, Any]) -> str:
    return _update_named(ctx, args, "characters", "char", "character")


def _update_world(ctx: AgentContext, args: dict[str, Any]) -> str:
    return _update_named(ctx, args, "world", "world", "world")


def _save_memory(ctx: AgentContext, args: dict[str, Any]) -> str:
    kind = args["kind"]
    if kind not in MEMORY_KINDS:
        raise ToolExecutionError("INVALID_MEMORY_KIND")
    if len(args["text"]) > 50_000:
        raise ToolExecutionError("CONTENT_TOO_LARGE: memory entry 超过 50000 字符")
    rel = memory_target_for_kind(kind)
    path = ctx.project.store.safe_path(ctx.project.id, rel)
    old = path.read_text(encoding="utf-8") if path.is_file() else ""
    if args["text"].strip() and args["text"].strip() in old:
        raise ToolExecutionError("DUPLICATE_MEMORY_ENTRY: 相同条目已经存在")
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    new = old.rstrip() + f"\n\n## {stamp}\n\n{args['text'].strip()}\n"
    return _mutate(ctx, operation="ai.save_memory_entry", rel=rel, text=new,
                   expected=args["expected_revision"], kind="memory")


def register_write_tools(registry) -> None:
    registry.register(ToolDef("update_outline", "更新完整大纲文档；必须携带刚读取的 revision。", {
        "type":"object", "properties":{"scope":{"type":"string"}, "number":{"type":"integer"},
        "text":{"type":"string"}, "expected_revision":{"type":"string"}},
        "required":["scope","text","expected_revision"]}, _update_outline,
        read_only=False, mutates_project=True))
    common = {"type":"object", "properties":{"name":{"type":"string"}, "text":{"type":"string"},
              "expected_revision":{"type":"string"}, "slug":{"type":"string"}},
              "required":["name","text","expected_revision"]}
    registry.register(ToolDef("update_character", "创建或更新完整人物设定。", common, _update_character,
                              read_only=False, mutates_project=True))
    registry.register(ToolDef("update_world", "创建或更新完整世界观设定。", common, _update_world,
                              read_only=False, mutates_project=True))
    registry.register(ToolDef("save_memory_entry", "向允许的派生记忆文档追加带时间戳条目。", {
        "type":"object", "properties":{"kind":{"type":"string"}, "text":{"type":"string"},
        "expected_revision":{"type":"string"}}, "required":["kind","text","expected_revision"]},
        _save_memory, read_only=False, mutates_project=True))
