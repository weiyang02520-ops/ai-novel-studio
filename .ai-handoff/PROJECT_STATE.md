# Project State (单一事实源)

> HANDOFF.md / STATUS.md 由 checkpoint 基于本文件与 Git 事实生成。

## project_goal

开发个人版、本地优先、自带 API Key 的 AI 小说创作软件：项目资料工作台 + 主编 → Writer → Reviewer 创作流程。

## phase

**M5 FINAL CLOSEOUT complete. Awaiting External ChatGPT final M5 review.**

M6 NOT AUTHORIZED.

## real_external_provider

**REAL_EXTERNAL_PROVIDER = UNVERIFIED_MISSING_CONFIG**。未索取、打印或保存 Key；localhost HTTP 生产链路已通过，非阻塞。

## last_round

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

## verified

- `python -m pytest tests/ -v`: **530 passed, 5 skipped, 0 failed**; collected = 535。
- M5 unit/integration suite covers TaskCard, relevance, budget, stream, partial/resume, races/protection, DraftService, workflow and CLI parser.
- Local HTTP E2E: subprocess NEW plus length, rewrite/undo, continue/undo, interrupt/resume, stale race and manual 0-request matrix PASS。
- Existing M0-M4 provider、chapter/confirm/history、Chief 与 knowledge regressions PASS。
- Real external: **UNVERIFIED_MISSING_CONFIG**（非阻塞）。

## architecture

- `agents/task_card.py`, `agents/planner.py`, `agents/writer.py`: Chief-to-Writer contract and prose runner。
- `core/relevance.py`, `core/context_budget.py`: deterministic source selection and bounded Writer input。
- `agents/planner.py`: independently bounded Chief planning context, overflow retry, then separate JSON repair。
- `core/generation.py`: safe non-canonical partial workspace and exact-overlap merge。
- `core/ai_draft.py`: canonical AI draft transaction boundary。
- `core/write_workflow.py`: target validation → plan → context → stream → finalize orchestration。
- `core/locks.py`: cross-process per-chapter critical-section lock for application mutations。
- `adapters/cli/m5.py`: display/callback-only M5 CLI adapter。

## stable_decisions

- Writer writes prose only; workflow owns canonical persistence。
- AI drafts cannot confirm before READY; M5 never runs Reviewer transitions。
- AI ready confirm is a state-machine contract only; M5 exposes no command or workflow that produces READY。
- Full novel is never automatically injected; ContextBudget is mandatory。
- Interrupted generation uses partial workspace; partial is never canonical or history-bearing。
- Rewrite/continue/resume use revision guard + Snapshot; external draft edits win stale races。
- MutationService remains knowledge-only; AI DraftService remains chapter-draft-only。

## known_issues

- GitHub 仓库可见性此前实测为 public；未擅自变更。
- Linux headless 无凭据服务时 SecretStore 明确报 BACKEND_UNAVAILABLE，不降级明文。

## next

- P0: External ChatGPT final M5 Review。
- P1: M6 Reviewer SUPER BATCH；**NOT AUTHORIZED**。
