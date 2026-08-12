# Project State (单一事实源)

> HANDOFF.md / STATUS.md 由 checkpoint 基于本文件与 Git 事实生成。

## project_goal

开发个人版、本地优先、自带 API Key 的 AI 小说创作软件：项目资料工作台 + 主编 → Writer → Reviewer 创作流程。

## phase

**M4 SUPER BATCH implementation complete. Awaiting External ChatGPT M4 review.**

M5 NOT AUTHORIZED；Writer、章节生成与动态 ContextBudget 未开始。

## real_external_provider

**REAL_EXTERNAL_PROVIDER = UNVERIFIED_MISSING_CONFIG**。生产配置没有可用的 `secret_reference`；未索取、打印或保存 Key。localhost 真实 HTTP 生产链路已通过。

## last_round

- 新增统一 `core.mutation.MutationService`：raw-byte SHA256 revision、ABSENT create guard、stale/no-op/empty/NUL/size 校验、snapshot → atomic write → history commit → byte verify、失败 restore/discard、ROLLBACK_FAILED 诚实高严重度错误。
- 所有 AI 资料写入集中在四个 Chief 工具：`update_outline` / `update_character` / `update_world` / `save_memory_entry`；人物和世界观支持 H1 查找、确定性中文 hash slug、collision/ambiguity 防护、create 自动 H1。
- `ToolDef.mutates_project` + runtime 全批 preflight：mutation 必须单独一批；mutation+read 或多个 mutation 均 0 execute；弱模型写请求安全阻断。
- revision-aware reads、简短 unified diff、`--show-diff`、history show、AI mutation audit、`undo-last-change`。
- 知识工作台 CLI：outline/character/world/memory/rules/knowledge search；doctor 只读诊断；revisions 与内存 fact-source manifest。
- 共享 `ContextItem` / `collect_project_context` / bounded renderer；M3 weak fallback 已复用，默认不读取章节正文。
- Chief Prompt 已升级为 read-before-write、FACT_SOURCE > DERIVED_MEMORY、stale re-read、mutation 后 re-read；仍禁止 Writer/Reviewer/正文写入/删除/shell。

## verified

- `python -m pytest tests/ -v`: **464 passed, 1 skipped, 0 failed**。
- Local HTTP E2E：CLI → create_provider → OpenAICompatibleProvider → HttpTransport → Chief → Registry → MutationService → filesystem/history，四轮 read → update → read → final PASS；undo 原字节恢复 PASS。
- Mutation：create/update/undo/stale/no-op/empty/NUL/limit/write failure/post-write verify/history commit rollback/rollback failure PASS。
- Character/World/Memory create/update/append/undo 与 stable slug/H1 PASS。
- Batch rejection、weak-model read regression/write block、M0/M1/M2/M3 regressions PASS。
- Knowledge search/doctor/revisions、ContextCollector priority/bounding/no chapter dump PASS。
- Real external: **UNVERIFIED_MISSING_CONFIG**（非阻塞）。

## architecture

- `core/mutation.py`: AI 写事务唯一入口；现有 Snapshot 是唯一 undo 机制。
- `core/knowledge.py`: 只读索引、搜索、doctor、revision、fact manifest。
- `core/context.py`: M4/M5 共用上下文来源层，不含动态 Writer budget。
- `tools/write_tools.py`: 四个 M4 mutation tools；不接受任意磁盘 path。
- `tools/read_tools.py`: M3 reads + M4 world/rules/search/status reads。
- `adapters/cli/m4.py`: 知识工作台、诊断、audit、undo CLI。

## stable_decisions

- single mutation per LLM response batch；任何 mutation 与 read 同批也拒绝。
- optimistic revision guard 使用原始 bytes SHA256；create 使用 ABSENT。
- Snapshot/history 是唯一 undo 数据，不建立第二套 audit 数据库。
- FACT_SOURCE 永远高于 DERIVED_MEMORY；memory 不能反向覆盖正式设定。
- Knowledge Doctor 永远只读。
- ContextCollector 是 Chief fallback 与未来 Writer 的共享基础，但 M5 动态预算未实现。
- Writer 与 `write_chapter_draft` 仍禁止成为 Chief 生产工具。

## known_issues

- GitHub 仓库可见性此前实测为 public；未擅自变更。
- Linux headless 无凭据服务时 SecretStore 明确报 BACKEND_UNAVAILABLE，不降级明文。

## next

- P0: External ChatGPT M4 review。
- P1: M5 Writer Agent + chapter generation + dynamic ContextBudget；**NOT AUTHORIZED**。
