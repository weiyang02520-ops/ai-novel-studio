# CHATGPT Memory — AI Novel Studio 长期项目记忆

> 供外部 ChatGPT 快速恢复上下文。只记录长期有效事实, 不记录动态 commit SHA / 临时状态。

## [USER_STATED] 产品目标
个人版 AI 小说创作软件: 本地优先, 自带 API Key, 多 Agent(主编→Writer→Reviewer)辅助长篇小说创作。

## [DESIGN_DECISION] Local First
所有数据本地明文保存(文件系统 + JSON, 无数据库, 无云同步)。备份 = 拷贝目录。

## [DESIGN_DECISION] 用户自带 API
AI 调用全部走用户自己的 OpenAI 兼容 API(Base URL / API Key / Model 可自定义)。无会员/支付/官方模型商城/激活码/创作点。

## [DESIGN_DECISION] Core/Adapter
Core Engine(无 UI 依赖: project/chapter/storage/history, 未来 agents/llm/tools)与 Adapter 层(CLI 现在, GUI 未来)严格分离。Core 禁止 argparse/click/终端 IO。

## [DESIGN_DECISION] AI 不是视觉中心
软件以"可靠的本地写作数据闭环"为核心, AI 是增强。第一版范围克制(无手机端/自动发布/客服/Suno/视频生成)。

## [DESIGN_DECISION] Chief → Writer → Reviewer
最小创作流程: 用户指令 → 主编规划 → Writer 写作 → Reviewer 审稿 → 用户确认 → 保存。Reviewer PASS ≠ 用户接受(仅 READY)。

## [DESIGN_DECISION] 事实源 vs 派生记忆
Canonical Sources(章节正文/人物正式设定/世界观/大纲/创作规则)不可被 rebuild 覆盖; Derived Memory(摘要/状态/时间线/伏笔索引)可重建。

## [EXTERNAL_REVIEW] M0 FINAL PASS
M0(工程骨架/配置系统/SecretStore/CLI)经 External ChatGPT 两轮审核通过。

## [DESIGN_DECISION] Milestone 批量执行工作方式
里程碑(M1 起)采用完整 Batch 执行: 实现→测试→自修 bug→回归→集成验收→checkpoint→push→等待外部审核。不逐子任务往返。

## [DESIGN_DECISION] 章节状态机与 M6 方案 B
DRAFT → USER_CONFIRMED → CONFIRMED(手动章节)。AI 章节由 Writer 生成 DRAFT；M6 REVIEWING 仅是进程内 Workflow run state，不写 frontmatter/pending sidecar；PASS 后成为 READY，最后仍由用户显式 confirm 成 CONFIRMED。`review recover` 返回 `NO_PENDING_REVIEW`。auto_accept 默认 false。

## [DESIGN_DECISION] SecretStore
真实 API Key 只存系统凭据管理器(Windows Credential Manager / keyring), 环境变量为开发回退。settings.json 只存 secret_reference。Key 绝不进入 Git/日志/交接文档。

## [PROCESS] Checkpoint 规则
`.ai-handoff/PROJECT_STATE.md` 是单一事实源; `scripts/ai_checkpoint.py` 每次强制重写 HANDOFF/STATUS, 安全扫描(NUL 枚举 fail-closed + 路径拦截 + 二进制阻止 + 分块扫描)通过后才 commit/push。

## [DESIGN_DECISION] M4 安全资料编辑
Chief 对大纲、人物、世界观、派生记忆的 AI 写入统一经过 MutationService。每个 LLM batch 最多一个 mutation；原始 bytes SHA256 乐观锁防覆盖；现有 Snapshot/history 是唯一 undo/audit 数据源；no-op 不留历史。

## [DESIGN_DECISION] Knowledge 与 Context 基础
Knowledge Doctor 只读且不评分。FACT_SOURCE 高于 DERIVED_MEMORY。ContextCollector 是 Chief fallback、Writer 与 Reviewer 共用的资料来源层；Writer/Reviewer 使用严格 model-window budget，默认不注入全书正文。

## [DESIGN_DECISION] M4 Final Hardening
所有 project Chief chat 在支持工具时统一获得完整 M4 registry，不做关键词能力路由；弱模型显式只读。Memory read/write 共用 kind-target 映射。Mutation 在 snapshot 后 writer 前立即重检原始字节，race 时只 discard 而不 restore。人物/世界观 H1 是稳定 identity；no-op 为正常结果且不写 history。

## [DESIGN_DECISION] M5 Writer
M5 Writer generates revision-protected AI drafts from bounded project context；只生成 `origin=ai,status=draft`，不拥有 review/ready/confirm。

## [DESIGN_DECISION] M5 Final Closeout
Chief planning and Writer input are independently bounded; partial resume metadata is prompt-redacted; `--no-stream` uses a true non-stream Provider call. Confirm permits AI only at READY while preserving AI origin。

## [DESIGN_DECISION] M6 Reviewer
M6 Reviewer 已成为 M7 可复用的严格关卡：Reviewer read/analyze only，无工具且不改正文；ReviewService 独占 report/status persistence。PASS 要求无 BLOCKER/MAJOR，任何 Provider/parser/context/preflight/transaction 不确定性 fail-closed。Report 绑定精确 draft revision，READY 必须有 current matching strict PASS artifact；PASS 不自动 confirm。Reviewer context bounded 且不注入全书，截断不能 READY。

M7 Autonomous Creation Loop implementation complete，等待 External ChatGPT M7 review。普通入口为 `compose <project> [chapter]`：Chief 规划 → Writer draft → Reviewer；NEEDS_WORK 转成 evidence-free、最多20项/3 strengths/canonical 20k 的 RevisionFeedback DATA，再经 Chief 规划 Writer rewrite 和 re-review。Reviewer 调用次数严格 1–10；max rounds、deterministic preflight blocker、相同 major/blocker fingerprint stall、Writer body no-effect、external stale 或 interruption 都有明确停止状态。

M7 以 `workflow/.runs/chNNNN.compose.json` 保存最小 allowlisted metadata，可跨进程 resume。恢复会验证 instruction SHA256、max rounds、模型配置、draft revision、report hash/currentness 与 M5 partial mode/base；不保存 instruction 原文、正文、Prompt、Context、完整报告/evidence 或 secret。READY 清 active sidecar，ESCALATED 保留，reset-run 只删 sidecar。compose 永不 confirm、不推进 current_chapter、不改 confirmed 或知识事实源；用户仍需显式 `chapter confirm`。M8 **NOT AUTHORIZED / NOT STARTED**。
