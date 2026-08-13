# M7 Autonomous Creation Loop — Implemented Boundary

M7 is implemented and awaiting external review. This document records the shipped interface boundary; it does not authorize M8.

## Implemented flow

- `compose <project> [chapter]` coordinates Chief planning, Writer generation or bounded rewrite, and Reviewer evaluation.
- A fixed `for` loop enforces `max_review_rounds` in the range 1–10. There is no unbounded autonomous loop.
- `RevisionFeedback` is strict, evidence-free, capped, hashed, and rendered to Chief and Writer as untrusted DATA beneath formal project facts.
- A current `NEEDS_WORK` report may drive one bounded rewrite only after deterministic preflight is checked again.
- Repeated blocker/major fingerprints, non-rewriteable blockers, insufficient review context, no-effect rewrites, and exhausted rounds escalate instead of continuing blindly.

## Durable state and recovery

- `workflow/.runs/chNNNN.compose.json` stores an exact privacy-safe allowlist of hashes, phases, counters, model identifiers, and timestamps.
- It never stores prose, prompts, context, instructions, report evidence, credentials, or a full round trace.
- `compose --status` and `compose --reset-run` are offline and do not initialize providers or read secrets.
- `compose --resume` validates and reconciles the sidecar against canonical draft, report, and M5 partial state. External bytes win every stale race.
- READY removes the active compose sidecar. ESCALATED retains it for inspection and an explicit resume after the user fixes project data.

## Authority boundary

- M7 may call existing Chief, Writer, and Reviewer workflows; it does not grant Reviewer write tools or Writer review authority.
- Compose never confirms a chapter, advances `current_chapter`, edits confirmed chapters, or mutates outline, character, world, rules, or memory sources.
- Reviewer PASS produces READY only. The user must still run `chapter confirm <project> <chapter>` explicitly.
- Failures, malformed model output, context uncertainty, interruptions, stale revisions, and races never produce a false READY.

## Next milestone

M8 is **NOT AUTHORIZED** and **NOT STARTED**. Post-confirm memory automation, sessions, GUI, import/export, analytics, and any additional autonomous behavior remain future design topics only. See `M8_READINESS.md`.
