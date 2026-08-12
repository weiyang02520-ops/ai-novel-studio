"""M3 只读工具(全部 READ-ONLY; 绝不修改任何小说文件)。

- project_info / list_chapters / read_outline / read_character / search_memory
- 输出标记事实源(FACT_SOURCE)或派生记忆(DERIVED_MEMORY), 供 Chief Prompt 取舍
- 所有路径经 ProjectStore.safe_path(防 .. / 绝对路径 / symlink 逃逸)
- 输出统一由 ToolRegistry 截断
"""
from __future__ import annotations

import json
import hashlib
from pathlib import Path
from typing import Any, Optional

from agents.types import AgentContext
from core.storage import DataIntegrityError, format_chapter_filename, parse_chapter_number_from_filename
from tools.types import ToolDef, ToolExecutionError

# ── 辅助 ─────────────────────────────────────────────────

def _read_project_file(ctx: AgentContext, rel: str) -> tuple[str, bool]:
    """读项目内文件; (内容, 是否存在)。路径经 safe_path(防穿越/逃逸)。"""
    path = ctx.project.store.safe_path(ctx.project.id, rel)
    if not path.is_file():
        return "", False
    try:
        return path.read_text(encoding="utf-8"), True
    except (OSError, UnicodeError) as e:
        raise ToolExecutionError(f"无法读取 {rel}(内部错误)")
    except Exception:
        raise ToolExecutionError(f"无法读取 {rel}(内部错误)")


def _fact(rel: str, body: str, extra: str = "") -> str:
    return f"SOURCE: {rel}\nTYPE: FACT_SOURCE\n{extra}{body}"


def _fact_file(ctx: AgentContext, rel: str, body: str) -> str:
    path = ctx.project.store.safe_path(ctx.project.id, rel)
    revision = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else "ABSENT"
    return _fact(rel, body, f"REVISION_SHA256: {revision}\nCONTENT:\n")


# ── project_info(§46-48) ─────────────────────────────────

def _project_info(ctx: AgentContext, args: dict[str, Any]) -> str:
    p = ctx.project
    m = p.metadata
    # 只白名单安全字段(§47: 不返回整个 project.json)
    info = {
        "id": p.id,
        "name": p.name,
        "genre": p.genre,
        "status": p.status,
        "current_volume": m.get("current_volume"),
        "current_chapter": p.current_chapter,
        "writing_style": m.get("writing_style", ""),
        "format_version": m.get("format_version"),
    }
    body = json.dumps(info, ensure_ascii=False, indent=2)
    return _fact("project.json", body)


# ── list_chapters(§49-51) ────────────────────────────────

def _list_chapters(ctx: AgentContext, args: dict[str, Any]) -> str:
    from core.chapter import list_chapters as core_list
    items = core_list(ctx.project)
    rows = []
    for it in items:
        rows.append({
            "chapter": it.get("chapter"),
            "title": it.get("title", ""),
            "status": it.get("status"),
            "words": it.get("words", 0),
            "location": it.get("location"),
            "conflict": bool(it.get("conflict")),
        })
    body = json.dumps(rows, ensure_ascii=False, indent=2)
    return _fact("chapters/ + drafts/", body)


# ── read_outline(§52-58) ─────────────────────────────────

def _volume_filename(number: int) -> str:
    if not isinstance(number, int) or isinstance(number, bool) or number < 1:
        raise ToolExecutionError("INVALID_VOLUME: volume 必须是 >=1 的整数")
    return f"vol{number:03d}.md"


def _read_outline(ctx: AgentContext, args: dict[str, Any]) -> str:
    parts: list[str] = []
    volume = args.get("volume")
    chapter = args.get("chapter")

    if volume is None and chapter is None:
        # 无参数: 全书梗概 + 当前卷大纲 + 列出可用文件(§53)
        summary, ok = _read_project_file(ctx, "outline/summary.md")
        if ok:
            parts.append(_fact_file(ctx, "outline/summary.md", summary))
        else:
            parts.append("outline/summary.md 不存在")

        cur_vol = ctx.project.metadata.get("current_volume", 1)
        vol_rel = f"outline/volumes/{_volume_filename(int(cur_vol))}"
        vtext, vok = _read_project_file(ctx, vol_rel)
        parts.append(_fact_file(ctx, vol_rel, vtext) if vok else f"{vol_rel} 不存在\nREVISION_SHA256: ABSENT")

        # 列出可用文件(不读内容)
        from core.knowledge import safe_markdown_files
        vols = sorted(f.name for f in safe_markdown_files(ctx.project, ["outline/volumes"]))
        chs = sorted(f.name for f in safe_markdown_files(ctx.project, ["outline/chapters"]))
        parts.append("可用卷大纲: " + (", ".join(vols) if vols else "(无)"))
        parts.append("可用章细纲: " + (", ".join(chs) if chs else "(无)"))
        return "\n\n".join(parts)

    if volume is not None:
        rel = f"outline/volumes/{_volume_filename(int(volume))}"
        text, ok = _read_project_file(ctx, rel)
        if not ok:
            return f"NOT_FOUND: {rel} 不存在"
        parts.append(_fact_file(ctx, rel, text))

    if chapter is not None:
        if not isinstance(chapter, int) or isinstance(chapter, bool) or chapter < 1:
            raise ToolExecutionError("INVALID_CHAPTER: chapter 必须是 >=1 的整数")
        rel = f"outline/chapters/{format_chapter_filename(chapter)}.md"
        text, ok = _read_project_file(ctx, rel)
        if not ok:
            return f"NOT_FOUND: {rel} 不存在"
        parts.append(_fact_file(ctx, rel, text))

    return "\n\n".join(parts)


# ── read_character(§59-65) ───────────────────────────────

def _first_h1(text: str) -> str:
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("# ") and not s.startswith("## "):
            return s[2:].strip()
    return ""


def _read_character(ctx: AgentContext, args: dict[str, Any]) -> str:
    from core.knowledge import safe_markdown_files
    name = (args.get("name") or "").strip()
    if not name:
        raise ToolExecutionError("INVALID_NAME: name 必填(non-empty string)")

    # 第一优先: exact filename stem(§61)
    safe_rel = f"characters/{name}.md"
    try:
        direct = ctx.project.store.safe_path(ctx.project.id, safe_rel)
    except Exception:
        direct = None
    if direct is not None and direct.is_file():
        text, _ = _read_project_file(ctx, safe_rel)
        return _fact_file(ctx, f"characters/{name}.md", text)

    # 第二: H1 标题匹配显示名(§62)
    matches: list[str] = []
    for f in safe_markdown_files(ctx.project, ["characters"]):
        try:
            content = f.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            continue
        title = _first_h1(content)
        if title == name or title.startswith(name + "("):
            matches.append(f.name)
        elif title == name:
            matches.append(f.name)

    if len(matches) > 1:
        return f"AMBIGUOUS: 多个人物文件匹配 '{name}': {', '.join(sorted(matches))}(请用文件 slug 精确指定)"
    if len(matches) == 1:
        rel = f"characters/{matches[0]}"
        text, _ = _read_project_file(ctx, rel)
        return _fact_file(ctx, rel, text)

    return f"NOT_FOUND: 项目正式人物设定(characters/)中未找到 '{name}'\nREVISION_SHA256: ABSENT"


def _read_world(ctx: AgentContext, args: dict[str, Any]) -> str:
    from core.knowledge import first_h1, safe_markdown_files
    name = (args.get("name") or "").strip()
    files = safe_markdown_files(ctx.project, ["world"])
    if not name:
        rows = [{"slug": p.stem, "display_name": first_h1(p.read_text(encoding="utf-8")),
                 "size": p.stat().st_size, "revision": hashlib.sha256(p.read_bytes()).hexdigest()[:12]}
                for p in files]
        return _fact("world/", json.dumps(rows, ensure_ascii=False, indent=2))
    matches = [p for p in files if p.stem == name or first_h1(p.read_text(encoding="utf-8")) == name]
    if len(matches) > 1:
        return f"AMBIGUOUS: 多个世界观文件匹配 '{name}'"
    if not matches:
        return f"NOT_FOUND: world/ 中未找到 '{name}'\nREVISION_SHA256: ABSENT"
    rel = matches[0].relative_to(ctx.project.dir).as_posix()
    return _fact_file(ctx, rel, matches[0].read_text(encoding="utf-8"))


def _read_rules(ctx: AgentContext, args: dict[str, Any]) -> str:
    rel = "rules/writing_rules.md"
    text, ok = _read_project_file(ctx, rel)
    return _fact_file(ctx, rel, text) if ok else f"NOT_FOUND: {rel}\nREVISION_SHA256: ABSENT"


def _read_memory(ctx: AgentContext, args: dict[str, Any]) -> str:
    from core.memory import memory_target_for_kind
    try:
        rel = memory_target_for_kind(args.get("kind", ""))
    except ValueError as e:
        raise ToolExecutionError(str(e))
    text, ok = _read_project_file(ctx, rel)
    if not ok:
        return f"SOURCE: {rel}\nTYPE: DERIVED_MEMORY\nREVISION_SHA256: ABSENT\nNOT_FOUND"
    path = ctx.project.store.safe_path(ctx.project.id, rel)
    return (f"SOURCE: {rel}\nTYPE: DERIVED_MEMORY\nREVISION_SHA256: "
            f"{hashlib.sha256(path.read_bytes()).hexdigest()}\nCONTENT:\n{text}")


def _search_project_knowledge(ctx: AgentContext, args: dict[str, Any]) -> str:
    from core.knowledge import search_knowledge
    try:
        hits = search_knowledge(ctx.project, args["keyword"],
                                include_chapters=bool(args.get("include_chapters", False)),
                                max_hits=args.get("limit", 20))
    except ValueError as e:
        raise ToolExecutionError(f"INVALID_KEYWORD: {e}")
    return json.dumps(hits, ensure_ascii=False, indent=2)


def _inspect_knowledge_status(ctx: AgentContext, args: dict[str, Any]) -> str:
    from core.knowledge import inspect_knowledge_status
    return json.dumps(inspect_knowledge_status(ctx.project), ensure_ascii=False, indent=2)


# ── search_memory(§66-73) ────────────────────────────────

_MEMORY_MAX_FILES = 500      # §73: 防失控
_MEMORY_MAX_CHARS = 2_000_000
_MEMORY_SNIPPET_CHARS = 300
_MEMORY_DEFAULT_LIMIT = 8
_MEMORY_LIMIT_MAX = 20


def _search_memory(ctx: AgentContext, args: dict[str, Any]) -> str:
    keyword = (args.get("keyword") or "").strip()
    if not keyword:
        raise ToolExecutionError("INVALID_KEYWORD: keyword 必填(non-empty string)")
    limit = args.get("limit")
    if limit is None:
        limit = _MEMORY_DEFAULT_LIMIT
    if not isinstance(limit, int) or isinstance(limit, bool):
        raise ToolExecutionError("INVALID_LIMIT: limit 必须是整数")
    limit = max(1, min(limit, _MEMORY_LIMIT_MAX))

    from core.knowledge import safe_markdown_files
    pdir = ctx.project.store.project_dir(ctx.project.id)
    files = safe_markdown_files(ctx.project, ["memory"])
    if not files:
        return "SOURCE: memory/\nTYPE: DERIVED_MEMORY\n(记忆目录为空)"

    # 只搜 memory/(§67); 所有路径经 safe_path 解析(防 symlink 逃逸)
    keyword_lower = keyword.lower()
    hits: list[dict[str, Any]] = []
    scanned_files = 0
    scanned_chars = 0

    for f in files:
        scanned_files += 1
        if scanned_files > _MEMORY_MAX_FILES:
            break
        safe = f
        try:
            lines = safe.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeError):
            continue
        for i, line in enumerate(lines):
            scanned_chars += len(line)
            if scanned_chars > _MEMORY_MAX_CHARS:
                break
            if keyword_lower in line.lower():
                rel = f.relative_to(pdir).as_posix()
                hits.append({
                    "relative_path": rel,
                    "revision": hashlib.sha256(safe.read_bytes()).hexdigest(),
                    "line": i + 1,
                    "snippet": line.strip()[:_MEMORY_SNIPPET_CHARS],
                })
                break  # 每文件只取首个匹配行
        if len(hits) >= limit or scanned_chars > _MEMORY_MAX_CHARS:
            break

    body = json.dumps(hits, ensure_ascii=False, indent=2)
    return f"SOURCE: memory/\nTYPE: DERIVED_MEMORY\n(派生记忆, 不是事实源; 与正式设定冲突时以事实源为准)\n{body}"


# ── 注册 ─────────────────────────────────────────────────

def build_readonly_registry() -> Any:
    """构建 M3 只读工具注册表(白名单: 仅 5 个只读工具, 无写工具)。"""
    from tools.registry import ToolRegistry

    registry = ToolRegistry()
    registry.register(ToolDef(
        name="project_info",
        description="读取当前小说项目的元信息(书名/题材/状态/进度/写作风格)。回答项目整体状态问题前先调用。",
        parameters={"type": "object", "properties": {}, "required": []},
        handler=_project_info,
    ))
    registry.register(ToolDef(
        name="list_chapters",
        description="列出当前小说全部章节(章节号/标题/状态/字数/位置/冲突标记)。回答进度、章节状态问题前先调用。",
        parameters={"type": "object", "properties": {}, "required": []},
        handler=_list_chapters,
    ))
    registry.register(ToolDef(
        name="read_outline",
        description="读取大纲资料。无参数返回全书梗概 + 当前卷大纲 + 可用文件列表; volume=N 读 outline/volumes/volNNN.md; chapter=N 读 outline/chapters/chNNNN.md。",
        parameters={
            "type": "object",
            "properties": {
                "volume": {"type": "integer", "description": "卷号(>=1)"},
                "chapter": {"type": "integer", "description": "章号(>=1)"},
            },
            "required": [],
        },
        handler=_read_outline,
    ))
    registry.register(ToolDef(
        name="read_character",
        description="读取人物正式设定(characters/)。name 可以是文件 slug(如 lin-xiaoman)或显示名(如 林小满)。",
        parameters={
            "type": "object",
            "properties": {"name": {"type": "string", "description": "人物 slug 或显示名"}},
            "required": ["name"],
        },
        handler=_read_character,
    ))
    registry.register(ToolDef(
        name="search_memory",
        description="在派生记忆(memory/)中按关键字搜索。注意: 记忆是派生数据, 与正式设定冲突时以事实源(大纲/人物/正文)为准。",
        parameters={
            "type": "object",
            "properties": {
                "keyword": {"type": "string", "description": "搜索关键字"},
                "limit": {"type": "integer", "description": "最多返回条数(默认 8, 最大 20)"},
            },
            "required": ["keyword"],
        },
        handler=_search_memory,
    ))
    return registry


def build_chief_registry() -> Any:
    """M4 production registry: all grounded reads plus four safe mutations."""
    registry = build_readonly_registry()
    registry.register(ToolDef("read_world", "列出或读取正式世界观资料；name 省略时列索引。", {
        "type":"object", "properties":{"name":{"type":"string"}}, "required":[]}, _read_world))
    registry.register(ToolDef("read_rules", "读取正式写作规则及 revision。", {
        "type":"object", "properties":{}, "required":[]}, _read_rules))
    registry.register(ToolDef("read_memory", "按 kind 读取完整派生记忆及 revision；保存前后必须调用。", {
        "type":"object", "properties":{"kind":{"type":"string"}}, "required":["kind"]}, _read_memory))
    registry.register(ToolDef("search_project_knowledge", "安全搜索大纲、人物、世界观、规则、记忆；默认不搜正文。", {
        "type":"object", "properties":{"keyword":{"type":"string"},
        "include_chapters":{"type":"boolean"}, "limit":{"type":"integer"}}, "required":["keyword"]},
        _search_project_knowledge))
    registry.register(ToolDef("inspect_knowledge_status", "只报告知识资料存在性和数量，不评分。", {
        "type":"object", "properties":{}, "required":[]}, _inspect_knowledge_status))
    from tools.write_tools import register_write_tools
    register_write_tools(registry)
    return registry
