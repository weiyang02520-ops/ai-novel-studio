# M7 Autonomous Creation Loop Implementation Plan

> **For implementer:** Use TDD throughout. Write each failing test first, run it and observe the failure, then add only the minimal production code required.

**Goal:** Orchestrate existing Chief, Writer and Reviewer workflows into a bounded, resumable, revision-safe creation loop that stops at READY or a clear escalation without ever confirming automatically.

**Architecture:** `CreationWorkflow` is a fourth workflow, not a fourth creative agent. It owns only bounded control flow and a private atomic run sidecar, while M5/M6 workflows retain all planning, prose, review and canonical persistence authority.

**Tech Stack:** Python 3.11 stdlib, existing workflow/provider/context/history/storage abstractions, pytest and localhost OpenAI-compatible mock server.

## Execution status

- Tasks 1–6: **implemented and locally verified** through focused unit, integration and static/privacy suites.
- Task 7: **final localhost HTTP/static verification is coordinated by the main delivery task**; its final count belongs in PROJECT_STATE/REVIEW_REQUEST, not this plan.
- Task 8 documentation: **completed** in README, durable memories and M8 readiness.
- Task 8 final delivery: **pending main-thread full-suite result, checkpoint, push and External ChatGPT M7 review**.
- M8: **NOT AUTHORIZED / NOT STARTED**.

---

### Task 1: RevisionFeedback and deterministic loop policy

**Files:** create `core/revision_feedback.py`; test `tests/test_m7_feedback.py`.

1. Add failing tests for strict fields/types, evidence exclusion, 20 issue/20k character caps, severity ordering, three strengths, canonical deterministic hash/render, fixed rewriteable categories, deterministic non-rewriteable preflight codes, issue fingerprints, stall/progress and prompt-injection-as-DATA.
2. Run target tests and observe missing API failures.
3. Implement frozen bounded value objects and pure classification/fingerprint helpers.
4. Run target tests and relevant M6 report tests; commit.

### Task 2: Private compose run state

**Files:** create `core/compose_state.py`; test `tests/test_m7_resume.py`.

1. Add failing tests for exact allowlist, strict phase/final state/max-round fields, atomic safe-path write/load/delete, symlink/path rejection, privacy sentinels, corrupt/stale/mismatched state and reset byte invariants.
2. Implement `ComposeRunState`/`ComposeRunStore` with atomic JSON and canonical revision validation helpers; no history or prose.
3. Run target storage/security tests and commit.

### Task 3: Feed revision DATA through existing M5 path

**Files:** modify `core/write_workflow.py`, `agents/planner.py`, `agents/writer.py`, `core/context_budget.py`; test `tests/test_m7_feedback.py` and focused M5 regressions.

1. Add failing tests that Chief planning and Writer receive bounded REVIEW_FEEDBACK as DATA, formal CHAPTER_OUTLINE/RULES remain higher priority, current draft is present, review evidence is absent, and no feedback is copied into user instruction or persisted partial sidecar.
2. Extend `WriteRequest` with optional feedback, add planner/writer context items and shared render labels, and preserve resume privacy.
3. Run target plus all M5 tests; commit.

### Task 4: CreationWorkflow entry resolution and bounded loop

**Files:** create `core/creation_workflow.py`; test `tests/test_m7_workflow.py`.

1. Add failing tests for confirmed/manual/no-draft/draft/stale report/current NEEDS_WORK/current READY states, max rounds 1–10, initial PASS, one/two rewrite PASS, max-round escalation, non-rewriteable escalation, stall/progress and Writer no-effect.
2. Implement a fixed bounded loop over injected `WriteWorkflow`/`ReviewWorkflow` factories, body hashes, revision checks, rounds and usages; no direct provider/http/history/confirm/knowledge mutation.
3. Run M5/M6 workflow/service regressions and commit.

### Task 5: Interruption, durable resume and external races

**Files:** extend `core/creation_workflow.py`, `core/compose_state.py`; test `tests/test_m7_resume.py`.

1. Add failing tests for initial/rewrite Writer interruption and resume, Review interruption and retry, synthetic crash state, stale NEEDS_WORK re-review, external edit during writer/reviewer, mismatched instruction hash/config, already-ready/confirmed transitions, and run-state cleanup/retention policy.
2. Implement phase writes at stable boundaries and resume that re-derives canonical truth before choosing `new/rewrite/resume/review`.
3. Run race/partial/history regressions and commit.

### Task 6: Compose CLI, status/reset, usage and Doctor

**Files:** create `adapters/cli/m7.py`; modify `adapters/cli/main.py`, `core/knowledge.py`; test `tests/test_m7_cli.py`.

1. Add failing parser/behavior tests for `compose <project> [chapter]`, `--instruction`, `--title`, `--target-chars`, entity overrides, `--max-rounds`, `--review-instruction`, `--resume`, `--status`, `--reset-run`, `--show-rounds`, `--json` and mutual exclusions.
2. Verify status/reset are fully offline and preserve draft/report/partial/history bytes; summaries expose hashes/counts only; Provider lifecycle and role separation are correct.
3. Add Doctor tests for orphan/invalid/mismatched/impossible run states.
4. Implement thin CLI/Doctor integration and safe usage metadata aggregation; commit.

### Task 7: Localhost HTTP production-path E2E and static/privacy audit

**Files:** create `tests/test_m7_http_e2e.py`, `tests/test_m7_static.py`; minimally extend `tests/mock_server.py` only if needed.

1. Add subprocess HTTP tests for initial PASS+explicit confirm, fail→rewrite→PASS, max rounds, stall, interrupt→new-process resume, review stale and rewrite stale.
2. Assert exact request role/order/count, non-stream Chief/Reviewer, streaming Writer, final body bytes, current_chapter/confirmed invariants and smaller bounded retry behavior.
3. Add AST/privacy tests forbidding confirm, knowledge writes, direct HTTP/provider imports, second history, unbounded loops and sensitive run-state/log fields.
4. Run all M7 and M0–M6 regression groups; commit.

### Task 8: Independent review and delivery

1. Run per-task spec and quality reviews; fix every Critical/Important issue test-first and re-review.
2. Update README, AGENT_MEMORY, CHATGPT_MEMORY, `docs/context/M8_READINESS.md`, PROJECT_STATE and REVIEW_REQUEST; M8 NOT AUTHORIZED.
3. Run `python -m pytest tests/ -v`, static/privacy/security scans and continuity postflight if registration becomes available.
4. Run `python scripts/ai_checkpoint.py` exactly once, verify clean worktree and local HEAD equals `origin/main`.
