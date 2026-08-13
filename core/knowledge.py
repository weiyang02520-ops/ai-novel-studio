"""Read-only knowledge indexing, diagnostics and fact-source manifests."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Iterable

from .chapter import list_chapters
from .mutation import file_revision
from .project import Project
from .storage import DataIntegrityError

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
    return [{"relative_path": item["relative_path"], "sha256": item["sha256"], "size": item["size"],
             "type": "FACT_SOURCE"}
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
    def add(severity: str, code: str, message: str) -> None:
        issues.append({"severity": severity, "code": code, "message": message})
    # Reuse canonical project/chapter integrity checks.
    try:
        from .project import validate_project
        for message in validate_project(project.store, project.id):
            add("ERROR", "PROJECT_INTEGRITY", message)
    except Exception as e:
        add("ERROR", "PROJECT_METADATA", str(e))
    # Force strict history parsing; no repair is performed.
    try:
        from .history import list_history
        list_history(project)
    except Exception as e:
        add("ERROR", "BROKEN_HISTORY", str(e))
    cur_v = int(project.metadata.get("current_volume", 1) or 1)
    if not project.store.safe_path(project.id, f"outline/volumes/vol{cur_v:03d}.md").is_file():
        add("WARNING", "MISSING_CURRENT_VOLUME_OUTLINE", f"缺少当前卷 vol{cur_v:03d}.md")
    for rel, code in (("outline/summary.md", "MISSING_OUTLINE_SUMMARY"),
                      ("rules/writing_rules.md", "MISSING_WRITING_RULES")):
        if not project.store.safe_path(project.id, rel).is_file():
            add("WARNING", code, f"缺少 {rel}")
    for root, code in (("characters", "DUPLICATE_CHARACTER_H1"), ("world", "DUPLICATE_WORLD_H1")):
        titles: dict[str, list[str]] = {}
        for path in safe_markdown_files(project, [root]):
            try:
                title = first_h1(path.read_text(encoding="utf-8"))
            except UnicodeError:
                add("ERROR", "INVALID_UTF8", path.relative_to(project.dir).as_posix())
                continue
            if title:
                titles.setdefault(title, []).append(path.name)
        for title, names in titles.items():
            if len(names) > 1:
                add("ERROR", code, f"重复 H1 {title!r}: {', '.join(names)}")
    # Decode every safe markdown source; memory is derived but corruption is visible.
    for path in safe_markdown_files(project, SEARCH_ROOTS):
        try:
            path.read_text(encoding="utf-8")
        except UnicodeError:
            rel = path.relative_to(project.dir).as_posix()
            add("WARNING" if rel.startswith("memory/") else "ERROR", "INVALID_UTF8", rel)
    # M6 Reviewer owns AI READY transitions; Doctor validates the matching report below.
    drafts_dir = project.store.safe_path(project.id, "drafts")
    if drafts_dir.exists():
        from .chapter import parse_frontmatter
        from .review import ReviewError, report_rel, require_current_pass_report
        for path in sorted(drafts_dir.glob("*.draft.md")):
            try:
                meta, _ = parse_frontmatter(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, DataIntegrityError):
                continue  # Canonical project validation already reports malformed drafts.
            if meta.get("origin") != "ai":
                continue
            if meta.get("status") in {"user_confirmed", "confirmed"}:
                add("ERROR", "AI_DRAFT_INVALID_STATUS",
                    f"{path.name}: origin=ai 不允许 status={meta.get('status')}")
            state = meta.get("generation_state")
            if state not in {"complete", "truncated"}:
                add("ERROR", "AI_DRAFT_INVALID_GENERATION_STATE",
                    f"{path.name}: generation_state={state!r}")
            if meta.get("status") == "ready":
                chapter = int(meta["chapter"])
                # Inspect the lexical path before ProjectStore resolves symlinks.
                # A same-project symlink is still forbidden for review artifacts.
                lexical = project.dir / report_rel(chapter)
                current = lexical
                unsafe = False
                while current != project.dir and current.is_relative_to(project.dir):
                    if current.is_symlink():
                        unsafe = True
                        break
                    current = current.parent
                if unsafe:
                    add("ERROR", "UNSAFE_REVIEW_PATH",
                        f"ch{chapter:04d}: review report path contains a symlink")
                    continue
                try:
                    require_current_pass_report(project, chapter, file_revision(path))
                except ReviewError as exc:
                    if exc.code == "REVIEW_NOT_FOUND":
                        code = "AI_READY_REVIEW_MISSING"
                    elif exc.code == "STALE_REVIEW_REPORT":
                        code = "AI_READY_REVIEW_STALE"
                    else:
                        code = "AI_READY_REVIEW_INVALID"
                    add("ERROR", code, f"{path.name}: {exc}")
    # M7 compose sidecars are privacy-safe orchestration hints, never canonical truth.
    # Diagnose them without repairing or deleting user state.
    runs_dir = project.dir / "workflow" / ".runs"
    if runs_dir.is_symlink():
        add("ERROR", "UNSAFE_COMPOSE_RUN_PATH", "workflow/.runs contains a symlink")
    elif runs_dir.exists():
        from .compose_state import ComposeRunStore, ComposeStateError
        import re
        for run_path in sorted(runs_dir.glob("*.compose.json")):
            match = re.fullmatch(r"ch(\d{4})\.compose\.json", run_path.name)
            if run_path.is_symlink():
                add("ERROR", "UNSAFE_COMPOSE_RUN_PATH", run_path.name)
                continue
            if match is None:
                add("WARNING", "INVALID_COMPOSE_RUN_NAME", run_path.name)
                continue
            chapter = int(match.group(1))
            try:
                state = ComposeRunStore(project, chapter).require()
            except ComposeStateError as exc:
                add("WARNING", exc.code, f"{run_path.name}: {exc.message}")
                continue
            draft = project.dir / f"drafts/ch{chapter:04d}.draft.md"
            confirmed = project.dir / f"chapters/ch{chapter:04d}.md"
            if not draft.exists() and not confirmed.exists():
                add("WARNING", "ORPHAN_COMPOSE_RUN", run_path.name)
            if state.phase == "READY":
                try:
                    meta, _ = parse_frontmatter(draft.read_text(encoding="utf-8"))
                    canonical_ready = meta.get("origin") == "ai" and meta.get("status") == "ready"
                except (OSError, UnicodeError, DataIntegrityError):
                    canonical_ready = False
                if not canonical_ready:
                    add("WARNING", "COMPOSE_READY_STATE_MISMATCH", run_path.name)
    # Explicitly report symlinks that cannot stay inside the project.
    for path in project.dir.rglob("*"):
        if path.is_symlink():
            try:
                if not path.exists():
                    add("ERROR", "BROKEN_SYMLINK", path.relative_to(project.dir).as_posix())
                elif not path.resolve().is_relative_to(project.dir.resolve()):
                    add("ERROR", "SYMLINK_ESCAPE", path.relative_to(project.dir).as_posix())
            except OSError:
                add("ERROR", "BROKEN_SYMLINK", str(path.name))
    return issues
