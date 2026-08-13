# M6 Reviewer Readiness — Fulfilled

This M5-to-M6 handoff is now fulfilled by the runnable M6 Reviewer workflow.

- `review <project> [chapter]` builds strict bounded context and runs a tool-free Reviewer.
- `ReviewReport` is strict JSON with fixed verdict, severity and category enums; malformed output receives at most one schema-only repair.
- Deterministic preflight findings merge with model issues and cannot be erased by a model PASS.
- `ReviewService` binds the report to the exact canonical draft revision and owns `draft -> ready|draft`, artifact persistence, history and rollback.
- Architecture B keeps canonical status `draft` during the network call; REVIEWING is process-local, no pending sidecar is persisted, and `review recover` returns `NO_PENDING_REVIEW`.
- Reviewer operations preserve prose body bytes. Draft/report races fail closed and preserve external bytes.
- PASS only produces READY. A current strict PASS artifact is required before the user can explicitly run `chapter confirm`.

Current status: M6 implementation complete; awaiting External ChatGPT M6 review.

This was the M6 handoff boundary. M7 has since been implemented and is awaiting external review; see `M7_READINESS.md`. M8 remains NOT AUTHORIZED. Automatic confirmation, outline/knowledge mutation, and post-confirm memory workflow still do not exist.
