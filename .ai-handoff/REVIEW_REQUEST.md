# External Review Request — M5 Writer

请审核 M5 Writer SUPER BATCH；M6 Reviewer **NOT AUTHORIZED**。

## 重点路径

- `agents/task_card.py`, `agents/planner.py`, `agents/writer.py`
- `core/relevance.py`, `core/context_budget.py`, `core/generation.py`
- `core/ai_draft.py`, `core/write_workflow.py`
- `adapters/cli/m5.py`, `adapters/cli/main.py`
- `tests/test_m5.py`, `tests/test_m5_http_e2e.py`

## Gate Matrix

- Chief TaskCard strict JSON repair/fallback and deterministic task hash
- Relevant entity resolution and bounded, strict rendered ContextBudget
- Prose-only streaming Writer with partial/interruption/resume
- New/rewrite/continue and exact-overlap merge
- AI DraftService raw-byte revision guard, Snapshot/history, rollback and undo
- `origin=ai`, `status=draft`, provenance frontmatter, current chapter unchanged
- Manual/confirmed protection and AI confirm block before READY
- Offline `context plan`, `write`, canonical draft and partial CLI
- localhost HTTP subprocess production path
- M0–M4 regression and secret/runtime artifact safety

请重点尝试 stale draft race、existing partial overwrite、tiny context window、stream tool-call、continue/rewrite resume 与 cleanup failure。
