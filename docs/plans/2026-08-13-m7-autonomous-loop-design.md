# M7 Autonomous Creation Loop Design

## Implementation status

**Implemented and locally verified. Awaiting External ChatGPT M7 review. M8 is not authorized.**

The production implementation lives in `core/revision_feedback.py`, `core/compose_state.py`, `core/creation_workflow.py` and `adapters/cli/m7.py`, with M5 context integration in the existing Writer path. The implementation retained the selected thin-orchestrator architecture; no fourth creative agent, direct Provider/HTTP dependency, second history, automatic confirmation or knowledge mutation was added.

## Scope

M7 composes the existing Chief, Writer and Reviewer into a bounded autonomous loop. It may create and rewrite an AI draft, but never confirms a chapter, mutates formal knowledge, overwrites confirmed prose, or advances `current_chapter`. M8 remains unauthorized.

## Considered architectures

1. **Thin durable orchestrator over existing workflows (selected).** `CreationWorkflow` resolves canonical state, calls `WriteWorkflow` and `ReviewWorkflow`, and persists only an allowlisted run state after stable boundaries. RevisionFeedback is a bounded DATA object passed through the existing Chief/Writer context path. This preserves all M5/M6 transaction and race guarantees.
2. **A new monolithic creation agent.** Rejected because it duplicates planning/review logic, weakens tool boundaries, and obscures per-stage revisions.
3. **A queue/event engine with database-backed state.** Rejected as unnecessary for a local single-user milestone and forbidden overengineering.

## Core contracts

- `RevisionFeedback` contains at most 20 executable non-INFO issues, prioritized BLOCKER/MAJOR/MINOR, plus at most three strengths to preserve. It excludes evidence and is capped at 20,000 characters. BLOCKER/MAJOR findings are never silently removed to fit the cap; an unrepresentable critical set fails explicitly.
- `CreationRound` stores only hashes, revisions, verdict/count metadata, models, timestamps and writer mode.
- `ComposeRunStore` persists `workflow/.runs/chNNNN.compose.json` via safe-path atomic JSON with an exact allowlist. It stores instruction SHA-256, never instruction text, prose, prompt, context, report, evidence, credentials or authorization.
- `CreationWorkflow` uses a fixed `for review_round in range(1, max_rounds + 1)` loop. `max_review_rounds` means Reviewer calls and is strictly 1–10.

## Entry-state resolution

- confirmed: return `ALREADY_CONFIRMED`, zero models.
- no draft: run M5 `new`; interrupted output becomes `INTERRUPTED` and keeps the M5 partial.
- manual draft: `MANUAL_DRAFT_PROTECTED`.
- AI draft: review directly unless a current NEEDS_WORK report can safely feed the next rewrite.
- stale NEEDS_WORK: re-review current bytes before any rewrite.
- ready + current PASS: return READY with zero models.
- invalid/stale ready: fail closed; only a revision-safe reopen of an otherwise structurally valid AI ready draft may return it to draft for review.

## Loop and stop policy

Each completed canonical draft is reviewed. PASS returns READY. NEEDS_WORK is inspected for deterministic non-rewriteable preflight blockers; those escalate immediately. If rounds remain, bounded RevisionFeedback is passed as DATA to Chief revision planning and Writer rewrite. After rewrite, body SHA-256 must change before another Reviewer call. Equal consecutive BLOCKER/MAJOR fingerprint sets with no count reduction stop as `STALLED_REVIEW`. Exhausting Reviewer calls stops as `MAX_REVIEW_ROUNDS`.

## Resume and races

Run state is written before/after stable stage boundaries, not as a second undo log. Resume re-derives truth from canonical draft/report/partial files and validates chapter, instruction hash, configured max rounds, model identifiers, draft revision, report hash/verdict and phase consistency. Interrupted rewrites reconstruct bounded feedback from the current strict NEEDS_WORK artifact before resuming the M5 partial. Stale saved metadata never authorizes a rewrite. Existing M5/M6 revision guards ensure external bytes win during Writer or Reviewer calls. Reset deletes only the run sidecar; READY removes it and ESCALATED retains it for diagnosis.

## Usage and privacy

Creation results aggregate Chief, Writer and Reviewer `Usage` metadata across every call, including Reviewer repair calls. CLI logging persists only provider/model/token/duration metadata. Review feedback and run state are treated as untrusted DATA; formal facts retain higher context priority.

## Verification

TDD covers feedback bounds and injection, entry-state truth table, one/two rewrites, max rounds, non-rewriteable escalation, stall/no-effect, interruption/resume at each phase, stale draft/report races, sidecar privacy/schema/symlinks, status/reset, Doctor, CLI and real localhost subprocess HTTP sequences. Final verification is `python -m pytest tests/ -v`, static M8/authority audits, one checkpoint and local/remote SHA parity.
