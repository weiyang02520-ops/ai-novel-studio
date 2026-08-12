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

## [DESIGN_DECISION] 章节状态机(手动路径)
DRAFT → USER_CONFIRMED → CONFIRMED(手动章节); AI 章节(M5)必须 DRAFT → REVIEWING → READY → USER_CONFIRMED → CONFIRMED, 禁止绕过 Reviewer。auto_accept 默认 false。

## [DESIGN_DECISION] SecretStore
真实 API Key 只存系统凭据管理器(Windows Credential Manager / keyring), 环境变量为开发回退。settings.json 只存 secret_reference。Key 绝不进入 Git/日志/交接文档。

## [PROCESS] Checkpoint 规则
`.ai-handoff/PROJECT_STATE.md` 是单一事实源; `scripts/ai_checkpoint.py` 每次强制重写 HANDOFF/STATUS, 安全扫描(NUL 枚举 fail-closed + 路径拦截 + 二进制阻止 + 分块扫描)通过后才 commit/push。

## [DESIGN_DECISION] M4 安全资料编辑
Chief 对大纲、人物、世界观、派生记忆的 AI 写入统一经过 MutationService。每个 LLM batch 最多一个 mutation；原始 bytes SHA256 乐观锁防覆盖；现有 Snapshot/history 是唯一 undo/audit 数据源；no-op 不留历史。

## [DESIGN_DECISION] Knowledge 与 Context 基础
Knowledge Doctor 只读且不评分。FACT_SOURCE 高于 DERIVED_MEMORY。ContextCollector 是 M3 weak fallback 与未来 M5 Writer 共用的资料来源层；M4 不包含动态 Writer budget，也不生成章节正文。

## [DESIGN_DECISION] M4 Final Hardening
所有 project Chief chat 在支持工具时统一获得完整 M4 registry，不做关键词能力路由；弱模型显式只读。Memory read/write 共用 kind-target 映射。Mutation 在 snapshot 后 writer 前立即重检原始字节，race 时只 discard 而不 restore。人物/世界观 H1 是稳定 identity；no-op 为正常结果且不写 history。
