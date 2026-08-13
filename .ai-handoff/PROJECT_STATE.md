# Project State (单一事实源)

> HANDOFF.md / STATUS.md 由 checkpoint 基于本文件与 Git 事实生成。

## project_goal

开发个人版、本地优先、自带 API Key 的 AI 小说创作软件：项目资料工作台 + 主编 → Writer → Reviewer 创作流程。

## phase

**M6 REVIEWER SUPER BATCH implementation complete. Awaiting External ChatGPT M6 review.**

M7 NOT AUTHORIZED.

## real_external_provider

**REAL_EXTERNAL_PROVIDER = UNVERIFIED_MISSING_CONFIG**。未索取、打印或保存 Key；localhost HTTP 生产链路用于工程验收，真实外部 Provider 缺配置不阻塞交付。

## last_round

- Reviewer 无工具、只读分析；通过 `BaseProvider.chat()` 返回严格 ReviewReport。
- ReviewReport 提供固定 enum、大小上限、行号归一化、去重、canonical hash 与一次 schema-only repair；无法证明 PASS 时 fail-closed。
- Review Context 复用共享 ContextBudget，按实际 messages 计数；draft 最高优先、不注入全书，超大/重试截断阻止 READY。
- relevance 从 draft、章纲、卷纲、instruction 与显式 override 推断，不伪造 M5 TaskCard。
- Preflight 合并 Doctor、draft integrity、generation/status、confirmed conflict、空正文与 source ambiguity；模型不能删除 deterministic blocker。
- 采用方案 B：canonical draft 在网络调用期间保持 `status=draft`；REVIEWING 仅进程内，无 pending sidecar，recover 返回 `NO_PENDING_REVIEW`。
- ReviewService 以短 chapter lock、draft/report revision CAS、Snapshot/history、atomic write、post-write verify 与 guarded rollback 持久化报告和状态。
- PASS 才能 `draft → ready`；NEEDS_WORK 保持 draft；Reviewer 永不改正文、调用 Writer 或自动 confirm。
- AI ready 的 confirm 与 Doctor 都要求 schema/hash 合法且匹配当前 draft revision 的 PASS artifact。
- 新增 review CLI、Doctor、localhost HTTP E2E 与 privacy/static regression。

## verified

- M6 target、CLI、transaction、Doctor、localhost HTTP E2E 与旧里程碑 regression 已运行；最终全量精确测试数由交付主线程在 checkpoint 前回填。
- Real external: **UNVERIFIED_MISSING_CONFIG**（非阻塞）。

## architecture

- `agents/review_report.py`, `agents/reviewer.py`, `agents/prompts/reviewer_system.md`: strict report contract and provider-independent Reviewer。
- `core/review_context.py`, `core/review_preflight.py`: bounded context and deterministic fail-closed checks。
- `core/review.py`: revision-bound report/status transaction, reopen/inspect, Architecture-B no-pending recover contract。
- `core/review_workflow.py`: preflight → context → review/repair → merge → finalize；网络调用不持锁。
- `adapters/cli/m6.py`: display/routing-only M6 CLI adapter。
- `review/chNNNN.review.json`: current review artifact；不保存 prompt/full context/API key。

## stable_decisions

- Reviewer read/analyze only；ReviewService 独占 review/status persistence；正文 body 不变。
- PASS 必须没有 BLOCKER/MAJOR；任何不确定性 fail-closed。
- Report 绑定精确 draft revision；draft/report 外部修改使旧结果 stale，外部字节获胜。
- READY 必须有 current matching PASS；PASS 不 confirm，用户仍需显式 `chapter confirm`。
- Reviewer context 严格 bounded，不自动注入全书。
- 方案 B 不持久化 reviewing/pending；recover 明确 `NO_PENDING_REVIEW`。
- NEEDS_WORK 后只能由用户显式 `write --rewrite`；M7 才拥有自动 Writer↔Reviewer loop。

## known_issues

- GitHub 仓库可见性此前实测为 public；未擅自变更。
- Linux headless 无凭据服务时 SecretStore 明确报 BACKEND_UNAVAILABLE，不降级明文。
- 真实外部 Reviewer 模型质量未验证；Reviewer 是辅助工具，PASS 不代表文学质量客观满分。

## next

- P0: External ChatGPT M6 Review。
- P1: M7 Writer↔Reviewer orchestration；**NOT AUTHORIZED**。
