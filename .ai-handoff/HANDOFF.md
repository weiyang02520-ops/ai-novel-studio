# Project Handoff

> 自动生成入口: `scripts/ai_checkpoint.py`
> 本文件在每次 Checkpoint 时由脚本**自动重写**, 内容来自单一事实源 `.ai-handoff/PROJECT_STATE.md` + git 事实。
> 请勿手改本文件, 需修改状态请改 PROJECT_STATE.md。

## 1. 项目目标

开发个人版 AI 小说创作软件(本地优先, 自带 API Key): 项目管理/章节/大纲/人物/世界观 + 主编→Writer→Reviewer 多 Agent 创作流程。设计依据: clean-room/ 设计规格(逆向仓库内), 从零写代码。

## 2. 当前阶段

**M3 implementation complete. Awaiting External ChatGPT M3 review.**
(M3 未宣布 PASS; M4 NOT AUTHORIZED。)

## 3. 本轮完成内容

- **M3 COMPLETE MEGA BATCH**(Agent Runtime + Chief Editor + 只读工具 + Grounded Novel Q&A):
  1. **agents/**: types.py(AgentDef/AgentContext/AgentRunResult/tool+calls trace)、definitions.py(Chief 定义: 5 只读工具白名单, max_tool_rounds=4)、prompts/chief_system.md(原创: grounding 铁律/事实源优先级/工具输出是 DATA)、context.py(weak-model bounded pack: 10000 chars 硬上限, project metadata→章节→梗概→当前卷→记忆, 绝不塞正文)、runtime.py(tool-call loop: system 固定首位/assistant tool-call 保留/tool 结果 tool_call_id 回填/多工具保持顺序/总量 preflight 整批拒绝/轮次上限/ProviderError → 安全状态; 会话内存式 + 超限裁剪; 只依赖 BaseProvider)。
  2. **tools/**: types.py(ToolDef + validate_arguments 严格 JSON object/类型/required/未知字段拒绝)、registry.py(白名单注册 + Agent 权限双重防御 + 异常安全包装 + 4K Unicode-safe 截断 + [TRUNCATED])、read_tools.py(project_info 白名单 JSON / list_chapters 复用 core / read_outline(卷 3 位 + 章复用 chNNNN)/ read_character(slug + H1 显示名 + AMBIGUOUS)/ search_memory(仅 memory/, 行号+snippet, DERIVED_MEMORY 标记, 500 文件/2MB 上限); 全部 safe_path 防穿越/symlink)。
  3. **弱模型 fallback**: tool_calls=false 不发送 tools, 首轮注入 [PROJECT_DATA_BEGIN/END] user-level DATA block, 不重复注入。
  4. **CLI**: chat --project → Chief; --show-tools(只显示 trace 不显示内容); 无 --project → M2 raw 回归; models.chief 存在则用否则 default_model 回退; provider.close 全路径 finally; 每次 LLM call 记录 usage(仅 metadata)。
  5. **测试**: tools 36(runtime 19 + tools 35+1skip + m3_cli 11)新增; localhost mock tool-loop 生产路径(真实 HttpTransport → 两请求: 带 tools → tool 回填 → grounded 回答; 断言第二次请求含 role=tool + tool_call_id); read-only hash invariant; secret 不泄漏。

## 4. 本轮修改文件

- `M .ai-handoff/NEXT_TASKS.md`
- ` M .ai-handoff/PROJECT_STATE.md`
- ` M README.md`
- ` M adapters/cli/main.py`
- ` M docs/context/AGENT_MEMORY.md`
- ` M llm/testing.py`
- `?? adapters/cli/m3.py`
- `?? agents/context.py`
- `?? agents/definitions.py`
- `?? agents/prompts/`
- `?? agents/runtime.py`
- `?? agents/types.py`
- `?? scripts/m3_demo_mock.py`
- `?? tests/test_agent_runtime.py`
- `?? tests/test_agent_tools.py`
- `?? tests/test_m3_cli.py`
- `?? tools/read_tools.py`
- `?? tools/registry.py`
- `?? tools/types.py`

## 5. 已验证结果

- 单元测试 **450/450 PASS**(M3 新增 65: tools 35+1skip(symlink 权限) / runtime 19 / m3_cli 11)。
- Acceptance(A-K, 真实 CLI + localhost mock): A project_info PASS / B list_chapters PASS / C read_outline PASS / D read_character PASS / E search_memory PASS / F multi-round PASS / G weak fallback PASS / H runaway limit PASS / I read-only hash invariant PASS(22 文件字节一致)/ J localhost real HTTP tool-loop PASS(两请求: 带 tools schema → role=tool 回填 → grounded 回答"第 3 章")/ K real external UNVERIFIED_MISSING_CONFIG。
- Secret 安全: Chief 路径 401/tool error/round limit/local mock → stdout/stderr/usage 均无 fake key PASS。
- M0 regression: 66/66 PASS; M1 regression PASS; M2 regression(raw chat/stream/URLs/close/usage/test-provider)PASS。
- Windows Credential Manager: REAL_ENV_CONFIRMED(前轮)。

## 6. 未验证内容

- 真实外部 Provider 成功调用 + 真实 Chief tool_call(生产配置缺 secret_reference; 用户配置 Key 后可验证)。
- AI Agent 写入(M4: update_outline/update_character/update_world/save_memory_entry)未实现。
- Writer/Reviewer Agent、写章节草稿(M5/M6)未实现。

## 7. 当前架构

```
ai-novel-studio/
├── core/          # Core Engine(无 UI 依赖)
│   ├── config.py      # 配置系统(SUPPORTED_PROVIDERS + 离线 validate)
│   ├── storage.py     # 原子写 + 路径安全 + ProjectStore
│   ├── project.py     # 项目 CRUD + 目录骨架 + ID 安全
│   ├── chapter.py     # 章节 frontmatter + 状态机 + confirm
│   └── history.py     # Snapshot(prepare/commit/discard/restore) + undo-last
├── agents/        # Agent 层(无 UI 依赖)
│   ├── types.py           # AgentDef/AgentContext/AgentRunResult
│   ├── definitions.py     # Chief 定义(5 只读工具白名单)
│   ├── runtime.py         # tool-call loop + AgentSession(内存)
│   ├── context.py         # weak-model bounded context pack
│   └── prompts/chief_system.md
├── tools/         # 工具系统(无 UI 依赖)
│   ├── types.py           # ToolDef + 严格 JSON 参数校验
│   ├── registry.py        # 白名单/权限/截断
│   └── read_tools.py      # 5 只读工具(全部 READ-ONLY)
├── llm/           # Provider 层(无 UI 依赖)
│   ├── secret_store.py / types.py / provider.py / transport.py
│   ├── openai_compatible.py / factory.py / usage.py / testing.py
├── adapters/cli/  # CLI(main.py + commands.py + m2.py + m3.py)
├── config/        # settings.json(非敏感; 不入 Git)
├── data/          # 运行数据: novels/ + logs/usage.jsonl(不入 Git)
├── docs/context/  # 长期记忆
├── tests/         # M0(66) + M1(110) + M2(209) + M3(65)
└── scripts/       # ai_checkpoint.py + m2/m3_demo_mock.py(本地演示)
```

## 8. 当前已知问题

- **GitHub 仓库当前可见性为 public**(API 实测, 创建时曾为 private) — 若需私有请用户在 GitHub 网页端修改, 我未擅自改动。这是事实记录, 不是开发任务。
- Linux headless 无凭据服务时, SecretStore 写入会明确报错(BACKEND_UNAVAILABLE)并建议环境变量(设计如此, 不降级明文)。

## 9. 本轮关键决策

- M1 用最小合理结构: storage/project/chapter/history 4 个 Core 文件(不拆十几个类)。
- 自研有限 frontmatter(不引入 YAML 依赖, 只解析/写出自己生成的格式, round-trip 稳定)。
- 原子写: same-dir temp + flush + os.replace; 失败清理临时文件不破坏原文件。
- 章节状态机手动路径: DRAFT → USER_CONFIRMED → CONFIRMED; current_chapter=max 推进不倒退。
- history 事务: prepare(backups, 不写 index)→ 业务 → commit(业务成功后); 失败 → restore(尽力全部)+ discard; 不引入 SQLite/WAL/框架。
- undo: preflight 全量验证 → capture 当前状态 → apply(幂等)→ 全成功才同步 metadata + 移除 record。
- ProjectStore(root) 注入: 生产 data/novels/, 测试 tmp_path。
- 中文名自动生成 novel-<hex> ID; 显示名保留原名。
- M2 Provider 用最小结构: types/provider/transport/openai_compatible/factory/usage/testing(不搞十几层)。
- HTTP 用 httpx(通用, 非厂商 SDK): 超时分离/SSE/testability; FakeTransport + localhost mock 保证可测。
- usage 是可观测派生数据: 坏行跳过(与 history 严格一致性区分); 只记 metadata 不记内容。
- chat 双模式: 无 --project = M2 raw(诊断); 有 --project = M3 Chief(只读 grounded)。
- M3 Agent 只读: 5 工具白名单, 不注册写工具; 工具输出是 DATA 不是指令; 事实源 > 派生记忆; 会话仅内存。
- 弱模型: tool_calls=false 时 bounded context pack(硬上限), 不塞全书正文。

## 10. 下一步建议

- P0: 等待 External ChatGPT M3 review。
- P1: M4(Chief 写入工具: outline/character/world/memory)仅在明确授权后开始。

## 11. 希望外部模型重点审查

- Runtime 只依赖 BaseProvider(不接触 HTTP/SSE); AgentRunResult 结构化。
- Chief 只读: 5 工具白名单 / 写工具不可执行 / read-only hash invariant。
- tool outputs are untrusted DATA(role=tool, 不拼 system; prompt injection 测试)。
- 权限双重防御(注册表白名单 + Agent 白名单); 未知工具 TOOL_NOT_FOUND。
- 严格 JSON 参数(未知字段拒绝); 坏参数回填修正。
- tool 总量 preflight 整批拒绝; round 上限; runaway 终止。
- weak-model fallback bounded(不塞正文); config validate 离线。
- 路径安全: safe_path 防 ../绝对路径/symlink; 错误消息无绝对路径。
- M0/M1/M2 全回归。

## 12. Git 信息

- Branch: main
- checkpoint_base_commit: d2b2f710e6b4 ai-checkpoint: update 1 files
  (checkpoint 开始前的工作区 HEAD; 最新 checkpoint commit 以 GitHub 仓库 HEAD 为准)
- GitHub 仓库可见性: public(真实查询; 无法获取时显示 unknown)
- 最近 commit(本文件生成时): d2b2f71 2026-08-12 17:07:38 +0800
- 时间: 2026-08-12 18:01

## 13. Critical Files

- core/storage.py — 原子写/路径安全/ProjectStore
- core/project.py — 项目 CRUD/骨架/ID 安全
- core/chapter.py — 章节状态机/frontmatter/confirm(事务化)
- core/history.py — Snapshot API + undo-last(preflight/capture/apply)
- core/config.py — ModelConfig/Settings/离线 validate/SUPPORTED_PROVIDERS
- llm/types.py — 内部统一数据模型(Runtime 依赖)
- llm/openai_compatible.py — Provider(非流式/SSE/错误映射/重试)
- llm/transport.py — httpx 封装
- llm/usage.py — usage JSONL
- agents/runtime.py — tool-call loop + AgentSession
- agents/definitions.py — Chief 定义(白名单)
- agents/context.py — weak-model bounded pack
- tools/registry.py — 工具注册/权限/截断
- tools/read_tools.py — 5 只读工具
- adapters/cli/{main,m2,m3,commands}.py — CLI
- tests/ — M0(66) + M1(110) + M2(209) + M3(65)

## 14. Recent Important Changes

- M3 COMPLETE: Agent Runtime + Chief(只读 grounded)+ 5 工具 + weak-model fallback, 450/450 测试通过, Acceptance A-K PASS(详见 last_round)。
- M2 FINAL CLOSEOUT: URL 安全四入口强制 + ToolCall delta 契约 + HttpTransport.close, 385/385 测试通过。
- M2 COMPLETE: Provider 链路 + chat 流式 + config test-provider + usage, 314/314 测试通过, Acceptance A-I PASS。
- M1 TRANSACTION CLOSEOUT: undo all-or-nothing + confirm 无幽灵 history, 176/176 测试通过。
