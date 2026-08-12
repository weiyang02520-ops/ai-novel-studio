# Project Handoff

> 自动生成入口: `scripts/ai_checkpoint.py`
> 本文件在每次 Checkpoint 时由脚本**自动重写**, 内容来自单一事实源 `.ai-handoff/PROJECT_STATE.md` + git 事实。
> 请勿手改本文件, 需修改状态请改 PROJECT_STATE.md。

## 1. 项目目标

开发个人版、本地优先、自带 API Key 的 AI 小说创作软件：项目资料工作台 + 主编 → Writer → Reviewer 创作流程。

## 2. 当前阶段

**M4 FINAL HARDENING complete. Awaiting External ChatGPT final M4 review.**

M5 NOT AUTHORIZED；Writer、章节生成与动态 ContextBudget 未开始。

## 3. 本轮完成内容

- FINAL HARDENING：统一 Agent/CLI/context/doctor 安全读取边界；项目外 symlink 不读取。
- 新增 `read_memory` 与共享 memory kind→target/revision contract。
- Mutation snapshot 后 writer 前立即字节 recheck；race 只 discard，不 restore 外部内容。
- 所有 project Chief chat 统一完整 M4 registry；删除关键词 capability routing；弱模型显式只读。
- Doctor 复用 project validate，并补 UTF-8、缺失资料、symlink、JSON UX。
- ContextCollector 补 named world 与显式 bounded recent confirmed chapters；默认仍无正文。
- Character/world H1 identity guard；no-op 正常返回且 0 history；history metadata 正式 optional dict。

- 新增统一 `core.mutation.MutationService`：raw-byte SHA256 revision、ABSENT create guard、stale/no-op/empty/NUL/size 校验、snapshot → atomic write → history commit → byte verify、失败 restore/discard、ROLLBACK_FAILED 诚实高严重度错误。
- 所有 AI 资料写入集中在四个 Chief 工具：`update_outline` / `update_character` / `update_world` / `save_memory_entry`；人物和世界观支持 H1 查找、确定性中文 hash slug、collision/ambiguity 防护、create 自动 H1。
- `ToolDef.mutates_project` + runtime 全批 preflight：mutation 必须单独一批；mutation+read 或多个 mutation 均 0 execute；弱模型写请求安全阻断。
- revision-aware reads、简短 unified diff、`--show-diff`、history show、AI mutation audit、`undo-last-change`。
- 知识工作台 CLI：outline/character/world/memory/rules/knowledge search；doctor 只读诊断；revisions 与内存 fact-source manifest。
- 共享 `ContextItem` / `collect_project_context` / bounded renderer；M3 weak fallback 已复用，默认不读取章节正文。
- Chief Prompt 已升级为 read-before-write、FACT_SOURCE > DERIVED_MEMORY、stale re-read、mutation 后 re-read；仍禁止 Writer/Reviewer/正文写入/删除/shell。

## 4. 本轮修改文件

- `M .ai-handoff/NEXT_TASKS.md`
- ` M .ai-handoff/PROJECT_STATE.md`
- ` M README.md`
- ` M adapters/cli/m3.py`
- ` M adapters/cli/m4.py`
- ` M adapters/cli/main.py`
- ` M agents/context.py`
- ` M agents/definitions.py`
- ` M agents/prompts/chief_system.md`
- ` M core/chapter.py`
- ` M core/context.py`
- ` M core/history.py`
- ` M core/knowledge.py`
- ` M core/mutation.py`
- ` M core/project.py`
- ` M docs/context/AGENT_MEMORY.md`
- ` M docs/context/CHATGPT_MEMORY.md`
- ` M tests/test_m3_cli.py`
- ` M tests/test_m4.py`
- ` M tools/read_tools.py`
- ` M tools/write_tools.py`
- `?? core/memory.py`
- `?? docs/context/M5_READINESS.md`
- `?? tests/test_m4_hardening.py`

## 5. 已验证结果

- `python -m pytest tests/ -v`: **484 passed, 5 skipped, 0 failed**（Windows 无 symlink 权限用例明确 skip）。
- Local HTTP E2E：CLI → create_provider → OpenAICompatibleProvider → HttpTransport → Chief → Registry → MutationService → filesystem/history，四轮 read → update → read → final PASS；undo 原字节恢复 PASS。
- Mutation：create/update/undo/stale/no-op/empty/NUL/limit/write failure/post-write verify/history commit rollback/rollback failure PASS。
- Character/World/Memory create/update/append/undo 与 stable slug/H1 PASS。
- Batch rejection、weak-model read regression/write block、M0/M1/M2/M3 regressions PASS。
- Knowledge search/doctor/revisions、ContextCollector priority/bounding/no chapter dump PASS。
- Real external: **UNVERIFIED_MISSING_CONFIG**（非阻塞）。

## 6. 未验证内容

- (待填写)

## 7. 当前架构

- `core/mutation.py`: AI 写事务唯一入口；现有 Snapshot 是唯一 undo 机制。
- `core/knowledge.py`: 只读索引、搜索、doctor、revision、fact manifest。
- `core/context.py`: M4/M5 共用上下文来源层，不含动态 Writer budget。
- `tools/write_tools.py`: 四个 M4 mutation tools；不接受任意磁盘 path。
- `tools/read_tools.py`: M3 reads + M4 world/rules/search/status reads。
- `adapters/cli/m4.py`: 知识工作台、诊断、audit、undo CLI。

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
- checkpoint_base_commit: 7f0e27148d54 ai-checkpoint: add 6 new files
  (checkpoint 开始前的工作区 HEAD; 最新 checkpoint commit 以 GitHub 仓库 HEAD 为准)
- GitHub 仓库可见性: public(真实查询; 无法获取时显示 unknown)
- 最近 commit(本文件生成时): 7f0e271 2026-08-12 18:50:07 +0800
- 时间: 2026-08-12 19:33

## 13. Critical Files

- (待填写)

## 14. Recent Important Changes

- (待填写)
