# Project Handoff

> 自动生成入口: `scripts/ai_checkpoint.py`
> 本文件在每次 Checkpoint 时由脚本**自动重写**, 内容来自单一事实源 `.ai-handoff/PROJECT_STATE.md` + git 事实。
> 请勿手改本文件, 需修改状态请改 PROJECT_STATE.md。

## 1. 项目目标

Build a local-first, bring-your-own-key AI novel studio with a safe Chief → Writer → Reviewer creation workflow.

## 2. 当前阶段

**M7 AUTONOMOUS CREATION LOOP implementation complete. Awaiting External ChatGPT M7 review.**

M8 NOT AUTHORIZED and NOT STARTED.

## 3. 本轮完成内容

- Added strict bounded `RevisionFeedback`, without evidence, rendered as untrusted DATA below formal facts.
- Added fixed-round Chief → Writer → Reviewer orchestration with PASS, NEEDS_WORK, stall, no-effect, context-insufficient, stale, interrupt, and escalation handling.
- Added privacy-safe compose sidecar phases, offline status/reset, crash reconciliation, and cross-process Writer resume.
- Added production `compose` CLI, per-role usage accounting, explicit READY handoff, and no automatic confirmation.
- Added Doctor/static/privacy checks plus seven real subprocess localhost HTTP compose scenarios.

## 4. 本轮修改文件

- `M .ai-handoff/PROJECT_STATE.md`
- ` M .ai-handoff/REVIEW_REQUEST.md`

## 5. 已验证结果

- `python -m pytest tests/ -v`: **730 passed, 7 skipped, 0 failed**; collected = 737.
- M0–M6 regression PASS.
- M7 feedback/context/workflow/resume/CLI/Doctor/static/privacy suites PASS.
- Localhost subprocess production paths PASS: first PASS, fail→rewrite→PASS, exact max rounds, stall, SSE interrupt→resume, review stale, rewrite stale.
- Canonical body, confirmed chapters, knowledge sources, and `current_chapter` ownership boundaries PASS.
- Real external provider: **UNVERIFIED_MISSING_CONFIG** (non-blocking).

## 6. 未验证内容

- Real external Chief/Writer/Reviewer semantic quality: **UNVERIFIED_MISSING_CONFIG**.
- Literary quality is not a deterministic engineering gate; Reviewer PASS means no visible BLOCKER/MAJOR in the bounded context.

## 7. 当前架构

- `core/revision_feedback.py`: bounded structured feedback, hashes, fingerprints, rewriteability and stall policy.
- `core/creation_workflow.py`: fixed-round orchestration, durable reconciliation, race handling, and terminal-state policy.
- `core/compose_state.py`: exact privacy-safe sidecar schema plus offline status/reset.
- `core/write_workflow.py`: Chief/Writer feedback DATA integration and feedback-bound partial resume.
- `adapters/cli/m7.py`: compose production wiring, output, offline inspection, and usage logging.
- `workflow/.runs/chNNNN.compose.json`: hashes/counters/models/phases only; no prose, prompts, context, evidence, instructions, or secrets.

## 8. 当前已知问题

- Real external model behavior remains unverified without user configuration.
- The repository visibility previously appeared public; no visibility mutation was performed.

## 9. 本轮关键决策

- Formal project facts outrank review feedback; feedback outranks the current draft as a revision instruction source.
- Review evidence is never forwarded to Chief/Writer or stored in compose state.
- The loop is a bounded `for` loop; max review rounds are 1–10 and count Reviewer calls.
- Any uncertainty, malformed output, insufficient context, stale revision, or race fails closed and never produces READY.
- External bytes win stale races; resume reconciles only documented canonical/partial/report crash windows.
- PASS produces READY, never confirmation. Only explicit `chapter confirm` advances the project.
- Compose never mutates confirmed chapters, outlines, characters, world, rules, memory, or `current_chapter`.

## 10. 下一步建议

- P0: External ChatGPT M7 review.
- P1: Address only review findings within M7 scope.
- M8 remains NOT AUTHORIZED.

## 11. 希望外部模型重点审查

- Verify bounded feedback completeness, evidence exclusion, DATA labeling, overflow retry, and partial privacy.
- Verify compose phase reconciliation, mismatch non-mutation, ESCALATED resume, round accounting, and stale external-byte preservation.
- Verify no automatic confirm/knowledge mutation, no unbounded loop, and no M8 implementation.
- Verify CLI offline status/reset and all seven localhost HTTP scenarios.

## 12. Git 信息

- Branch: main
- checkpoint_base_commit: 697ef7dbc51b docs: retire the M6 pre-M7 boundary
  (checkpoint 开始前的工作区 HEAD; 最新 checkpoint commit 以 GitHub 仓库 HEAD 为准)
- GitHub 仓库可见性: public(真实查询; 无法获取时显示 unknown)
- 最近 commit(本文件生成时): 697ef7d 2026-08-13 16:27:56 +0800
- 时间: 2026-08-13 16:28

## 13. Critical Files

- `core/revision_feedback.py`, `core/creation_workflow.py`, `core/compose_state.py`
- `core/write_workflow.py`, `core/review_workflow.py`, `agents/reviewer.py`
- `adapters/cli/m7.py`, `adapters/cli/main.py`
- `tests/test_m7_*.py`, `tests/mock_server.py`
- `README.md`, `docs/context/AGENT_MEMORY.md`, `docs/context/M7_READINESS.md`, `docs/context/M8_READINESS.md`

## 14. Recent Important Changes

- Implemented M7 autonomous creation loop with strict authority and privacy boundaries.
- Added durable compose resume and crash reconciliation without storing creative text.
- Added production CLI, Doctor/static checks, and localhost end-to-end coverage.
- Updated durable documentation for M7 complete / M8 not authorized.
