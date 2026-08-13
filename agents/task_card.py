"""Structured Chief-to-Writer task cards."""
from __future__ import annotations
import dataclasses, hashlib, json, re
from typing import Any


class TaskCardError(ValueError): pass


@dataclasses.dataclass(frozen=True)
class WritingTaskCard:
    chapter: int
    goal: str
    target_chars: int
    title: str = ""
    opening: str = ""
    conflict: str = ""
    turning_point: str = ""
    ending_hook: str = ""
    characters: list[str] = dataclasses.field(default_factory=list)
    world_elements: list[str] = dataclasses.field(default_factory=list)
    continuity_requirements: list[str] = dataclasses.field(default_factory=list)
    style_requirements: list[str] = dataclasses.field(default_factory=list)
    forbidden_changes: list[str] = dataclasses.field(default_factory=list)
    user_instruction: str = ""
    chief_brief: str = ""
    source: str = "structured"

    def __post_init__(self):
        if not isinstance(self.chapter, int) or isinstance(self.chapter, bool) or self.chapter < 1:
            raise TaskCardError("chapter 必须是正整数")
        if not isinstance(self.goal, str) or not self.goal.strip():
            raise TaskCardError("goal 必填")
        if not isinstance(self.target_chars, int) or isinstance(self.target_chars, bool) or self.target_chars < 200:
            raise TaskCardError("target_chars 必须 >= 200")
        for name in ("title", "opening", "conflict", "turning_point", "ending_hook",
                     "user_instruction", "chief_brief", "source"):
            if not isinstance(getattr(self, name), str):
                raise TaskCardError(f"{name} 必须是 string")
        for name in ("characters","world_elements","continuity_requirements","style_requirements","forbidden_changes"):
            value = getattr(self, name)
            if not isinstance(value, list) or not all(isinstance(x, str) for x in value): raise TaskCardError(f"{name} 必须是 list[str]")

    def to_dict(self) -> dict[str, Any]: return dataclasses.asdict(self)
    def resume_dict(self) -> dict[str, Any]:
        """Minimum cross-process resume shape; excludes prompt-like fields."""
        excluded = {"user_instruction", "chief_brief"}
        return {key: value for key, value in self.to_dict().items() if key not in excluded}
    @property
    def task_hash(self) -> str:
        return hashlib.sha256(json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True,
                                         separators=(",", ":")).encode()).hexdigest()


def parse_task_card(text: str) -> WritingTaskCard:
    raw = text.strip()
    m = re.fullmatch(r"```(?:json)?\s*\n?(.*?)\n?```", raw, re.S | re.I)
    if m: raw = m.group(1).strip()
    try: data = json.loads(raw)
    except json.JSONDecodeError as e: raise TaskCardError(f"TASK_JSON_INVALID: {e.msg}") from e
    if not isinstance(data, dict): raise TaskCardError("TASK_JSON_NOT_OBJECT")
    allowed = {f.name for f in dataclasses.fields(WritingTaskCard)}
    unknown = set(data) - allowed
    if unknown: raise TaskCardError(f"TASK_UNKNOWN_FIELDS: {sorted(unknown)}")
    try: return WritingTaskCard(**data)
    except (TypeError, TaskCardError) as e: raise TaskCardError(f"TASK_SCHEMA_INVALID: {e}") from e
