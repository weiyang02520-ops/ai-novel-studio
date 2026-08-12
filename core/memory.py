"""Canonical derived-memory kind and target contract."""
MEMORY_TARGETS = {
    "long_term": "memory/long_term.md", "timeline": "memory/timeline.md",
    "characters": "memory/characters.md", "world": "memory/world.md",
    "index": "memory/index.md", "foreshadowing": "memory/foreshadowing/index.md",
}
MEMORY_KINDS = frozenset(MEMORY_TARGETS)


def memory_target_for_kind(kind: str) -> str:
    try:
        return MEMORY_TARGETS[kind]
    except KeyError as e:
        raise ValueError(f"INVALID_MEMORY_KIND: {kind!r}") from e
