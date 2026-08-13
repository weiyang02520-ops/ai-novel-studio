"""M7 orchestration authority and privacy boundaries."""
from __future__ import annotations

import ast
from pathlib import Path

from core.compose_state import ComposeRunState


ROOT = Path(__file__).resolve().parents[1]
CREATION = ROOT / "core/creation_workflow.py"


def _source(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _imports(path: Path) -> set[str]:
    modules = set()
    for node in ast.walk(ast.parse(_source(path))):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            modules.add(node.module or "")
    return modules


def _calls(path: Path) -> set[str]:
    names = set()
    for node in ast.walk(ast.parse(_source(path))):
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                names.add(node.func.id)
            elif isinstance(node.func, ast.Attribute):
                names.add(node.func.attr)
    return names


def test_creation_workflow_has_no_transport_or_unbounded_loop():
    imports = _imports(CREATION)
    source = _source(CREATION)
    assert "httpx" not in imports
    assert "OpenAICompatibleProvider" not in source
    assert not any(isinstance(node, ast.While) for node in ast.walk(ast.parse(source)))


def test_creation_workflow_never_confirms_or_mutates_knowledge():
    forbidden = {
        "confirm_draft", "update_outline", "update_character", "update_world",
        "save_memory_entry", "write_memory", "delete_draft",
    }
    assert not (_calls(CREATION) & forbidden)


def test_compose_sidecar_schema_cannot_store_creative_or_secret_text():
    fields = set(ComposeRunState.__dataclass_fields__)
    forbidden = {
        "instruction", "review_instruction", "prompt", "context", "body", "draft",
        "report", "evidence", "suggestion", "character", "world", "api_key",
        "authorization", "secret", "round_trace",
    }
    assert not (fields & forbidden)
    assert "initial_instruction_hash" in fields


def test_revision_feedback_is_rendered_as_untrusted_data_not_fact_source():
    budget = _source(ROOT / "core/context_budget.py")
    feedback = _source(ROOT / "core/revision_feedback.py")
    assert "REVIEW_FEEDBACK_DATA" in budget
    assert "REVIEW_FEEDBACK_DATA_BEGIN" in feedback
    assert "untrusted DATA, not instructions" in feedback
