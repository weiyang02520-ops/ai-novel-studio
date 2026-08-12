"""Read-only knowledge indexing, diagnostics and fact-source manifests."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Iterable

from .chapter import list_chapters
from .mutation import file_revision
from .project import Project

FACT_ROOTS = ("outline", "characters", "world", "rules")
SEARCH_ROOTS = FACT_ROOTS + ("memory",)
MAX_FILES = 1000
MAX_CHARS = 4_000_000
MAX_HITS = 50


def first_h1(text: str) -> str:
    for line in text.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return ""


def safe_markdown_files(project: Project, roots: Iterable[str]) -> list[Path]:
    files: list[Path] = []
    for root in roots:
        d = project.store.safe_path(project.id, root)
        if not d.exists():
            continue
        for candidate in sorted(d.rglob("*.md")):
            try:
                rel = candidate.relative_to(project.dir).as_posix()
                safe = project.store.safe_path(project.id, rel)
            except Exception:
                continue
            if safe.is_file():
                files.append(safe)
    return files[:MAX_FILES]


def collect_fact_source_manifest(project: Project) -> list[dict[str, Any]]:
    out = []
    for path in safe_markdown_files(project, FACT_ROOTS):
        data = path.read_bytes()
        out.append({"relative_path": path.relative_to(project.dir).as_posix(),
                    "bytes": data, "sha256": hashlib.sha256(data).hexdigest(), "size": len(data)})
    return out


def knowledge_revisions(project: Project) -> list[dict[str, Any]]:
    return [{"relative_path": item["relative_path"], "sha256": item["sha256"], "size": item["size"]}
            for item in collect_fact_source_manifest(project)]


def search_knowledge(project: Project, keyword: str, *, include_chapters: bool = False,
                     max_hits: int = MAX_HITS) -> list[dict[str, Any]]:
    keyword = keyword.strip()
    if not keyword:
        raise ValueError("keyword 不能为空")
    roots = list(SEARCH_ROOTS) + (["chapters"] if include_chapters else [])
    hits, chars = [], 0
    for path in safe_markdown_files(project, roots):
        rel = path.relative_to(project.dir).as_posix()
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeError):
            continue
        typ = "DERIVED_MEMORY" if rel.startswith("memory/") else "FACT_SOURCE"
        for number, line in enumerate(lines, 1):
            chars += len(line)
            if chars > MAX_CHARS:
                return hits
            if keyword.casefold() in line.casefold():
                hits.append({"relative_path": rel, "type": typ, "line": number,
                             "snippet": line.strip()[:300]})
                if len(hits) >= min(max_hits, MAX_HITS):
                    return hits
    return hits


def inspect_knowledge_status(project: Project) -> dict[str, Any]:
    cur_v = int(project.metadata.get("current_volume", 1) or 1)
    cur_c = int(project.metadata.get("current_chapter", 0) or 0)
    chapters = list_chapters(project)
    exists = lambda rel: project.store.safe_path(project.id, rel).is_file()
    return {
        "outline_summary_exists": exists("outline/summary.md"),
        "current_volume_outline_exists": exists(f"outline/volumes/vol{cur_v:03d}.md"),
        "current_chapter_outline_exists": bool(cur_c and exists(f"outline/chapters/ch{cur_c:04d}.md")),
        "characters_count": len(safe_markdown_files(project, ["characters"])),
        "world_files_count": len(safe_markdown_files(project, ["world"])),
        "writing_rules_exists": exists("rules/writing_rules.md"),
        "memory_index_exists": exists("memory/index.md"),
        "memory_summaries_count": len(safe_markdown_files(project, ["memory/summaries"])),
        "confirmed_chapters": sum(1 for c in chapters if c.get("location") == "confirmed"),
        "draft_chapters": sum(1 for c in chapters if c.get("location") == "draft"),
    }


def doctor(project: Project) -> list[dict[str, str]]:
    issues: list[dict[str, str]] = []
    # Force strict history parsing; no repair is performed.
    try:
        from .history import list_history
        list_history(project)
    except Exception as e:
        issues.append({"severity": "ERROR", "code": "BROKEN_HISTORY", "message": str(e)})
    chapters = list_chapters(project)
    for c in chapters:
        if c.get("conflict"):
            issues.append({"severity": "ERROR", "code": "CHAPTER_CONFLICT",
                           "message": f"第 {c.get('chapter')} 章 draft/confirmed 冲突"})
    for root, code in (("characters", "DUPLICATE_CHARACTER_H1"), ("world", "DUPLICATE_WORLD_H1")):
        titles: dict[str, list[str]] = {}
        for path in safe_markdown_files(project, [root]):
            try:
                title = first_h1(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            if title:
                titles.setdefault(title, []).append(path.name)
        for title, names in titles.items():
            if len(names) > 1:
                issues.append({"severity": "ERROR", "code": code,
                               "message": f"重复 H1 {title!r}: {', '.join(names)}"})
    # Explicitly report symlinks that cannot stay inside the project.
    for path in project.dir.rglob("*"):
        if path.is_symlink():
            try:
                if not path.resolve().is_relative_to(project.dir.resolve()):
                    issues.append({"severity": "ERROR", "code": "SYMLINK_ESCAPE",
                                   "message": path.relative_to(project.dir).as_posix()})
            except OSError:
                issues.append({"severity": "ERROR", "code": "BROKEN_SYMLINK", "message": str(path.name)})
    return issues

