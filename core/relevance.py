"""Deterministic relevant character/world selection."""
from __future__ import annotations
import json
from dataclasses import dataclass, field
from .knowledge import first_h1, safe_markdown_files

class RelevanceError(ValueError): pass

@dataclass(frozen=True)
class RelevantEntities:
    characters: list[str]
    world: list[str]
    reasons: dict[str, list[str]] = field(default_factory=dict)

def build_relevance_source(project, chapter: int, card, instruction: str = "") -> str:
    """Shared offline/online entity evidence; project text is DATA only."""
    volume = int(project.metadata.get("current_volume", 1))
    chunks = [json.dumps(card.to_dict(), ensure_ascii=False, sort_keys=True)]
    for rel in (f"outline/chapters/ch{chapter:04d}.md",
                f"outline/volumes/vol{volume:03d}.md"):
        path = project.store.safe_path(project.id, rel)
        if path.is_file():
            chunks.append(path.read_text(encoding="utf-8"))
    if instruction:
        chunks.append(instruction)
    return "\n\n".join(chunks)

def _index(project, root):
    out = []
    for p in safe_markdown_files(project, [root]):
        title = first_h1(p.read_text(encoding="utf-8"))
        out.append((p.stem, title, p.relative_to(project.dir).as_posix()))
    return out

def resolve_relevant_entities(project, card, source_text: str, *, characters=None, world=None):
    reasons, selected_c, selected_w = {}, [], []
    def resolve(index, requested, code, kind):
        chosen=[]
        for name, reason in requested:
            matches=[x for x in index if x[0].casefold()==name.casefold() or x[1].casefold()==name.casefold()]
            if not matches: raise RelevanceError(f"{code}_NOT_FOUND: {name}")
            if len(matches)>1: raise RelevanceError(f"AMBIGUOUS_{code}: {name}")
            rel=matches[0][2]
            if rel not in chosen: chosen.append(rel); reasons.setdefault(rel,[]).append(reason)
        haystack=source_text.casefold()
        for slug,title,rel in index:
            if (title and title.casefold() in haystack) or slug.casefold() in haystack:
                if rel not in chosen: chosen.append(rel); reasons.setdefault(rel,[]).append("AUTO")
        return sorted(chosen)
    creq=[(x,"TASK_CARD") for x in card.characters]+[(x,"MANUAL") for x in (characters or [])]
    wreq=[(x,"TASK_CARD") for x in card.world_elements]+[(x,"MANUAL") for x in (world or [])]
    selected_c=resolve(_index(project,"characters"),creq,"CHARACTER","character")
    selected_w=resolve(_index(project,"world"),wreq,"WORLD","world")
    return RelevantEntities(selected_c, selected_w, reasons)
