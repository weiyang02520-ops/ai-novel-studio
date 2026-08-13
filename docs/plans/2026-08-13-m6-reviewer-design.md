# M6 Reviewer Design

## Scope and authorization

M6 adds a read/analyze-only Reviewer gate between an AI draft and explicit user confirmation. M7 orchestration, automatic rewriting, automatic confirmation, and post-confirm memory updates remain forbidden.

## Considered architectures

1. **In-memory run state + short revision-guarded transactions (selected, Architecture B).** The canonical draft stays `status=draft` while the network call runs. REVIEWING is process-local; no pending sidecar is persisted. Begin and finalize each take a short chapter lock and compare exact raw-byte revisions. A crash leaves canonical files unchanged, so recover returns `NO_PENDING_REVIEW`.
2. **Persist `status=reviewing` in the draft.** This makes the state visible in frontmatter, but creates an extra draft revision, more rollback paths, and a higher chance of a stranded canonical file after interruption.
3. **Hold the chapter lock across the Provider call.** This is simple but blocks writers and user operations for an unbounded network duration and is rejected by the specification.

## Components

- `agents/review_report.py`: strict report schema, enums, raw/fenced JSON parsing, caps, line normalization, deterministic deduplication/severity ordering, PASS policy, and canonical hash.
- `agents/reviewer.py`: provider-independent non-stream `ReviewerRunner`; `tools=None`; one schema-only JSON repair; no persistence.
- `core/review_context.py`: review-specific source collection and rendering while reusing `core.context_budget.plan_context`; the draft receives highest priority and oversized drafts use deterministic head/middle/tail slices marked `DRAFT_TRUNCATED_FOR_REVIEW`.
- `core/review_preflight.py`: deterministic integrity checks merged with model issues. Deterministic BLOCKER/MAJOR findings cannot be erased by the model.
- `core/review.py`: `ReviewService` owns in-memory run guards, exact-revision begin/finalize, report persistence, snapshot/history rollback, stale detection, no-pending recover, reopen, and inspection.
- `adapters/cli/m6.py`: display/callback-only review adapter. `adapters/cli/main.py` only parses and routes.

## Data flow

1. Resolve an AI `status=draft`, capture exact raw bytes and revision, run deterministic preflight, resolve relevant entities, and build a strict context plan.
2. `--plan-only` stops here with zero Provider/status/report/history mutation.
3. Under a short chapter lock, recheck the revision and register a process-local run guard; do not mutate canonical draft/report files.
4. Release the lock and call Reviewer once. On the first context overflow rebuild at `0.65` and retry once. On malformed JSON perform one schema-only repair.
5. Merge deterministic and model issues, normalize verdict, and bind the canonical report to the original exact draft revision and context hash.
6. Under a short lock, recheck draft and prior-report revisions. A mismatch returns a stale result with zero overwrite. Otherwise snapshot draft/report, atomically persist report and status transition, commit history, verify bytes, then release the in-memory run guard.
7. PASS produces `ready`; NEEDS_WORK or any deterministic blocker produces `draft`. No path confirms a chapter or calls Writer.

## Failure and recovery rules

- Any Provider, parser, repair, context, interruption, revision, report-race, or transaction uncertainty is fail-closed: never READY.
- Cleanup/rollback uses compare-and-swap semantics and never restores over external bytes.
- A crash leaves no persisted REVIEWING/pending metadata and canonical files remain unchanged; `review recover` returns `NO_PENDING_REVIEW`.
- A truncated draft cannot prove PASS and is normalized to NEEDS_WORK with a contextual limitation issue.
- Report JSON is capped before parsing/persistence; evidence is capped at 300 characters and usage logs never receive prose, report, prompt, or context.

## State and confirmation boundary

The canonical state remains `draft` during review, then becomes `ready` only at a successful final transaction (or remains `draft`). REVIEWING is process-local, not persisted frontmatter. READY is valid only when the latest report is PASS, contains no BLOCKER/MAJOR, and is bound to the current exact draft revision. Explicit `chapter confirm` remains the only path to `confirmed`.

## Verification strategy

Use TDD for schema/runner, context/preflight, service transactions/races/recovery, CLI/doctor/undo, and localhost HTTP subprocess E2E. Assert byte-identical prose across review operations, exact revision/report binding, one shrink retry with request 2 smaller than request 1, and zero READY on every uncertain outcome. Finish with `python -m pytest tests/ -v`, static import/tool/M7 audits, one checkpoint, push, and local/remote SHA equality.
