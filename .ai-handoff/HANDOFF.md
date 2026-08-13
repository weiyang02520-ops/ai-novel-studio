# Project Handoff

> 自动生成入口: `scripts/ai_checkpoint.py`
> 本文件在每次 Checkpoint 时由脚本**自动重写**, 内容来自单一事实源 `.ai-handoff/PROJECT_STATE.md` + git 事实。
> 请勿手改本文件, 需修改状态请改 PROJECT_STATE.md。

## 1. 项目目标

开发个人版、本地优先、自带 API Key 的 AI 小说创作软件：项目资料工作台 + 主编 → Writer → Reviewer 创作流程。

## 2. 当前阶段

**M5 FINAL CLOSEOUT complete. Awaiting External ChatGPT final M5 review.**

M6 NOT AUTHORIZED.

## 3. 本轮完成内容

- Chief 输出严格 `WritingTaskCard`，支持一次 JSON repair 与安全 fallback；Writer AgentDef 无工具且只输出正文。
- Relevant Entity Resolver 合并 TaskCard、文本自动识别与 CLI override，并拒绝 missing/ambiguous identity。
- Dynamic ContextBudget 提供优先级、裁剪/丢弃 manifest、确定性 context hash 与一次 context-too-long shrink。
- Writer 使用真实 `stream_chat`；正文逐 chunk flush 到非 canonical partial，支持中断、跨进程 resume、continue 与 rewrite。
- `AIChapterDraftService` 使用 raw-byte revision guard、Snapshot/history、atomic write、post-write verify 与 rollback；只写 `origin=ai, status=draft`。
- 新增 `write`、`context plan`、`draft`/`draft partial` CLI；context plan 完全离线。
- AI draft confirm guard、frontmatter provenance、chapter list 与 Knowledge Doctor 检查已接入。
- Final hardening counts rendered context wrappers strictly, keeps newest recent chapters first, refuses partial overwrite, and budgets continuation tails.
- Per-chapter cross-process locks close application-level confirm/finalize/partial-prepare races; stale external bytes still win.
- Stream protocol tool calls are hard failures even after text; public TaskCard output excludes opaque Chief brief.
- Chief Planner now has a strict, prioritized context plan using the Chief model window and one independent 0.65 overflow retry.
- AI confirm branches by origin: manual draft/user_confirmed, AI ready only, with confirmed origin preserved; no READY transition exists in M5.
- Partial sidecar stores a redacted resume card and original task hash, never instruction/Chief brief/full context.
- `--no-stream` performs a true non-stream Provider `chat()` call; offline context planning shares workflow relevance evidence.

## 4. 本轮修改文件

- `M .ai-handoff/NEXT_TASKS.md`
- ` M .ai-handoff/PROJECT_STATE.md`
- ` M .ai-handoff/REVIEW_REQUEST.md`
- ` M README.md`
- ` M adapters/cli/m5.py`
- ` M agents/planner.py`
- ` M agents/task_card.py`
- ` M agents/writer.py`
- ` M core/chapter.py`
- ` M core/context_budget.py`
- ` M core/relevance.py`
- ` M core/write_workflow.py`
- ` M docs/context/AGENT_MEMORY.md`
- ` M docs/context/CHATGPT_MEMORY.md`
- ` M docs/context/M6_READINESS.md`
- ` M tests/test_m5.py`
- ` M tests/test_m5_http_e2e.py`

## 5. 已验证结果

- `python -m pytest tests/ -v`: **530 passed, 5 skipped, 0 failed**; collected = 535。
- M5 unit/integration suite covers TaskCard, relevance, budget, stream, partial/resume, races/protection, DraftService, workflow and CLI parser.
- Local HTTP E2E: subprocess NEW plus length, rewrite/undo, continue/undo, interrupt/resume, stale race and manual 0-request matrix PASS。
- Existing M0-M4 provider、chapter/confirm/history、Chief 与 knowledge regressions PASS。
- Real external: **UNVERIFIED_MISSING_CONFIG**（非阻塞）。

## 6. 未验证内容

- (待填写)

## 7. 当前架构

- `agents/task_card.py`, `agents/planner.py`, `agents/writer.py`: Chief-to-Writer contract and prose runner。
- `core/relevance.py`, `core/context_budget.py`: deterministic source selection and bounded Writer input。
- `agents/planner.py`: independently bounded Chief planning context, overflow retry, then separate JSON repair。
- `core/generation.py`: safe non-canonical partial workspace and exact-overlap merge。
- `core/ai_draft.py`: canonical AI draft transaction boundary。
- `core/write_workflow.py`: target validation → plan → context → stream → finalize orchestration。
- `core/locks.py`: cross-process per-chapter critical-section lock for application mutations。
- `adapters/cli/m5.py`: display/callback-only M5 CLI adapter。

## 8. 当前已知问题

- GitHub 仓库可见性此前实测为 public；未擅自变更。
- Linux headless 无凭据服务时 SecretStore 明确报 BACKEND_UNAVAILABLE，不降级明文。

## 9. 本轮关键决策

- (待填写)

## 10. 下一步建议

- (待填写)

## 11. 希望外部模型重点审查

- (待填写)

## 12. Git 信息

- Branch: main
- checkpoint_base_commit: b8dad3485b0e ai-checkpoint: update 13 files
  (checkpoint 开始前的工作区 HEAD; 最新 checkpoint commit 以 GitHub 仓库 HEAD 为准)
- GitHub 仓库可见性: public(真实查询; 无法获取时显示 unknown)
- 最近 commit(本文件生成时): b8dad34 2026-08-12 20:56:16 +0800
- 时间: 2026-08-13 14:14

## 13. Critical Files

- (待填写)

## 14. Recent Important Changes

- (待填写)
