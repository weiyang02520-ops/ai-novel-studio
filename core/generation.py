"""Crash-resilient, non-canonical Writer partial workspace."""
from __future__ import annotations

import datetime
import json

from .storage import atomic_write_json, atomic_write_text, format_chapter_filename
from .locks import chapter_lock


def generation_paths(project, chapter: int):
    stem = format_chapter_filename(chapter)
    return (
        project.store.safe_path(project.id, f"drafts/.generation/{stem}.partial.md"),
        project.store.safe_path(project.id, f"drafts/.generation/{stem}.partial.json"),
    )


class GenerationWorkspace:
    def __init__(self, project, chapter: int):
        self.project = project
        self.chapter = chapter
        self.partial, self.sidecar = generation_paths(project, chapter)

    def prepare(self, metadata: dict) -> None:
        with chapter_lock(self.project, self.chapter):
            self.partial.parent.mkdir(parents=True, exist_ok=True)
            if self.partial.exists() or self.sidecar.exists():
                raise FileExistsError("PARTIAL_EXISTS")
            # Sidecar never contains prompts, project context, secrets, or prose.
            try:
                atomic_write_text(self.partial, "")
                atomic_write_json(
                    self.sidecar,
                    {
                        **metadata,
                        "chapter": self.chapter,
                        "created_at": datetime.datetime.now(datetime.timezone.utc).strftime(
                            "%Y-%m-%dT%H:%M:%SZ"
                        ),
                    },
                )
            except Exception:
                for path in (self.partial, self.sidecar):
                    try:
                        path.unlink(missing_ok=True)
                    except OSError:
                        pass
                raise

    def update_metadata(self, **changes) -> None:
        data = self.metadata()
        atomic_write_json(self.sidecar, data | changes)

    def append(self, text: str) -> None:
        with self.partial.open("a", encoding="utf-8", newline="") as handle:
            handle.write(text)
            handle.flush()

    def text(self) -> str:
        return self.partial.read_text(encoding="utf-8") if self.partial.is_file() else ""

    def metadata(self) -> dict:
        data = json.loads(self.sidecar.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or data.get("chapter") != self.chapter:
            raise ValueError("INVALID_PARTIAL_SIDECAR")
        return data

    def cleanup(self) -> list[str]:
        warnings: list[str] = []
        for path in (self.partial, self.sidecar):
            try:
                if path.exists():
                    path.unlink()
            except OSError:
                warnings.append(f"无法清理 {path.name}")
        return warnings


def list_partials(project) -> list[dict]:
    directory = project.store.safe_path(project.id, "drafts/.generation")
    if not directory.exists():
        return []
    out: list[dict] = []
    for candidate in sorted(directory.glob("ch*.partial.json")):
        try:
            rel = candidate.relative_to(project.dir).as_posix()
            safe = project.store.safe_path(project.id, rel)
            data = json.loads(safe.read_text(encoding="utf-8"))
            body, _ = generation_paths(project, int(data["chapter"]))
            out.append(data | {"chars": len(body.read_text(encoding="utf-8")) if body.exists() else 0})
        except (OSError, UnicodeError, ValueError, TypeError, KeyError, json.JSONDecodeError):
            continue
    return out


def merge_continuation(existing: str, continuation: str, max_overlap: int = 2000) -> str:
    limit = min(max_overlap, len(existing), len(continuation))
    overlap = 0
    for size in range(1, limit + 1):
        if existing[-size:] == continuation[:size]:
            overlap = size
    tail = continuation[overlap:]
    if not tail:
        return existing
    if existing and not existing.endswith(("\n", " ")) and not tail.startswith(("\n", " ")):
        return existing + "\n" + tail
    return existing + tail
