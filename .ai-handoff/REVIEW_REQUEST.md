# External Review Request — M7 Autonomous Creation Loop

## Gate

M7 implementation is complete and awaiting external review. M8 is NOT AUTHORIZED and NOT STARTED.

## Verification

- Full suite: `python -m pytest tests/ -v`
- Result: **730 passed, 7 skipped, 0 failed** (737 collected)
- Real external provider: **UNVERIFIED_MISSING_CONFIG**; localhost subprocess HTTP production paths are verified.

## Review matrix

1. RevisionFeedback is strict, <=20 issues, <=20k canonical chars, evidence-free, hashed, and cannot silently drop BLOCKER/MAJOR findings.
2. Chief and Writer receive complete feedback only as untrusted DATA; overflow/truncation fails closed.
3. Compose uses a fixed bounded loop and does not auto-confirm or mutate knowledge/confirmed chapters/current_chapter.
4. NEEDS_WORK reuse reruns deterministic preflight; blocker/context limitations cannot be erased.
5. Stall/no-effect/max-round/non-rewriteable/context-insufficient paths escalate without another blind write.
6. Sidecar has an exact non-prose allowlist; status/reset are offline; resume validates/reconciles canonical draft, report, partial, model, instruction hash, and counters.
7. Mismatch does not destroy resumability; crash windows reconcile; external bytes win stale review/rewrite races.
8. Reviewer repair usage includes both calls; CLI records each Chief/Writer/Reviewer call without prompt/context/report text.
9. READY requires a current exact PASS artifact and still requires explicit `chapter confirm`.
10. Seven localhost HTTP scenarios exercise the real subprocess CLI/provider/budget/workflow/filesystem path.

## Critical files

- `core/revision_feedback.py`
- `core/creation_workflow.py`
- `core/compose_state.py`
- `core/write_workflow.py`
- `adapters/cli/m7.py`
- `tests/test_m7_feedback*.py`
- `tests/test_m7_workflow.py`
- `tests/test_m7_resume.py`
- `tests/test_m7_http_e2e.py`
- `tests/test_m7_static.py`

## Explicit prohibitions

- No automatic chapter confirmation.
- No automatic outline/character/world/rules/memory mutation.
- No unbounded loop or M8 workflow.
- No prose, prompts, context, evidence, instructions, credentials, or full reports in compose sidecars/usage logs.
