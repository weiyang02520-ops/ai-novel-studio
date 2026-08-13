# M6 Reviewer Readiness

- M5 Writer output is always `origin=ai` and `status=draft`.
- Generation provenance records state, mode, model, context hash, and TaskCard hash.
- `AIChapterDraftService` owns revision-protected canonical AI draft persistence.
- Chief produces a validated `WritingTaskCard`; Writer produces prose only.
- `ContextBudgetPlan` is mandatory before every Writer call and never injects the full novel by default.
- Interrupted generation stays in the non-canonical `drafts/.generation/` workspace and supports cross-process resume.
- Rewrite and continue use raw-byte revision guards plus the existing Snapshot/history undo mechanism.
- Chapter-scoped cross-process locks serialize application confirm/finalize/partial preparation critical sections.
- A future Reviewer owns only `DRAFT → REVIEWING → READY`; Reviewer PASS is not user confirmation.
- Confirm boundary structurally accepts only AI `ready` and preserves `origin=ai`; M5 exposes no READY transition, so Writer drafts remain blocked.

**M6 NOT AUTHORIZED.** No runnable Reviewer workflow is present.
