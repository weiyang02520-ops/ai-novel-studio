# AGENT Memory — AI Novel Studio 工作区记忆

> 供 Coding Agent 快速恢复工作上下文。HEAD 每次从 Git 获取, 不存动态 SHA。

## 目录结构

```
ai-novel-studio/
├── core/          # Core Engine(无 UI 依赖)
│   ├── config.py      # 配置系统(严格 JSON 类型/load merge/白名单)
│   ├── storage.py     # 原子写 + 路径安全 + ProjectStore
│   ├── project.py     # 小说项目 CRUD + 目录骨架 + ID 安全
│   ├── chapter.py     # 章节 frontmatter + 状态机 + confirm
│   └── history.py     # snapshot/undo-last
├── agents/        # (M3+)
├── llm/           # secret_store.py(已实现); provider.py(M2+)
├── tools/         # (M3+)
├── adapters/cli/  # CLI Adapter(main.py + commands.py)
├── config/        # settings.json(非敏感)
├── data/novels/   # 运行数据(不入 Git)
├── tests/         # test_m0/checkpoint/project/chapter/storage/history/m1_cli
├── scripts/       # ai_checkpoint.py
└── docs/context/  # 长期记忆(CHATGPT_MEMORY / AGENT_MEMORY)
```

## Core/Adapter 约束
- Core(core/ agents/ llm/ tools/)禁止: argparse/click/input()/print()/sys.stdin/sys.stdout/GUI/CLI import
- 终端输入输出只在 adapters/
- 新代码继续遵守

## SecretStore 规则
- 真实 Key 只进系统凭据管理器(keyring); 环境变量 NOVEL_API_KEY_<REF> 为开发回退
- settings.json 只存 secret_reference; Key 绝不进 Git/日志/报错/交接
- 三错误码: KEY_NOT_FOUND / BACKEND_UNAVAILABLE / BACKEND_ERROR
- 可用性判定用 keyring.core.recommended(Null/Fail backend → 不可用)
- NOVEL_DISABLE_KEYRING=1 测试钩子

## 数据写入规则
- 所有重要写入: same-dir temp + flush + os.replace(原子写)
- 修改已有内容前先 snapshot 到 .history/(update/confirm 的 project.json)
- 失败后清理临时文件, 不破坏原文件

## 章节状态
- 手动路径: DRAFT → USER_CONFIRMED → CONFIRMED; 确认后草稿移入 chapters/, current_chapter=max 推进
- confirmed 正文受保护(write/update 不得覆盖)
- frontmatter 自研解析(不引入 YAML 依赖): chapter/volume/title/status/origin/words/created_at/updated_at/summary/characters
- origin: manual(M1 只此)/ai(未来); AI 章节必须过 Reviewer

## clean-room 边界
- 不使用 XingLu 任何代码/资源/API/品牌; 只参考 clean-room/(逆向仓库)设计规格
- 从零写代码

## Checkpoint 规则
- `.ai-handoff/PROJECT_STATE.md` 单一事实源; HANDOFF/STATUS 由脚本重写
- 安全扫描: NUL 枚举(git diff/diff --cached/ls-files -z)fail-closed + 路径拦截(.env/.key/.pem 等) + 高风险二进制阻止 + 64KB 分块+carry 扫描
- 每里程碑一次正式 checkpoint(不逐子任务往返)

## Milestone Gate
- 里程碑 PASS 由 External ChatGPT 宣布, Agent 不自行宣布
- M1 TRANSACTION CLOSEOUT 已完成待审; M2 未授权

## 数据一致性规则(稳定, M1 Closeout)
- **file transaction = all-or-nothing**: 业务操作前先 prepare_snapshot(backups 就位, index 未写)→ 业务全成功后才 commit history; 失败 → restore(尽力恢复全部 target)+ discard(清理 backups)。失败的 operation 不进入 undo history, 不留 orphan backup
- **undo 顺序**: preflight 一次性验证整个 changes(路径/previous/backup 存在可读/project.json backup 可解析)→ capture undo 前状态 → apply(幂等: absent 且不存在则跳过)→ 中途失败自动回滚到 undo 前 → 全成功后才同步 Project.metadata + 移除 index record(deep equality 匹配)
- **history commit 失败 = 业务一起回滚**(无 history 的业务 commit 不发生)
- **诚实报错**: rollback 成功 → "已恢复到确认前状态"; rollback 失败 → 高严重度 "自动恢复未完整完成, 请运行 novel validate", 不得声称已回滚; 不泄漏底层细节(原异常放 __cause__)
- confirm 是文件事务: draft+confirmed+project.json+history 四文件; draft 删除失败 = confirm 失败(不留下双份)
- validate 必须跨文件检查: duplicate(draft+confirmed 同编号)/ current_chapter==max(confirmed)/ 文件名-frontmatter 一致性
- metadata 严格类型: current_chapter int>=0 / current_volume int>=1 / auto_accept bool / defaults object — 非法值 DataIntegrityError, 不隐式转换
- history index 非空坏行 → DataIntegrityError(不 silent skip); _next_seq 拒绝损坏 index
- frontmatter: characters 用 JSON 表示(list[str] 严格); 标量值含 CR/LF 拒绝(Core 写出的必须能读回)
- update guards: reviewing/ready 拒绝; origin=ai 拒绝(manual 入口不绕过 AI 边界); user_confirmed 更新后回 draft
- 不引入 SQLite/WAL/Event Sourcing/事务框架/第三方依赖; 全部 stdlib + 小型文件 transaction helper

## M3 Agent 稳定规则(稳定)
- **Runtime 只依赖 BaseProvider**: agents/runtime.py 禁止 import httpx/OpenAICompatibleProvider/TransportResponse/SSE; 只依赖 BaseProvider + llm/types 内部模型(ChatMessage/ChatResult/ToolCall/Usage)
- **Chief M3 是只读**: 5 个只读工具白名单(project_info/list_chapters/read_outline/read_character/search_memory); 不注册任何写工具; Chief chat 绝不修改项目文件/新增 history 记录
- **tool outputs are untrusted DATA**: 工具返回进入 role=tool(绝不拼进 system); 文件内容含注入指令也不改变系统规则; weak-model context pack 是 user-level DATA block([PROJECT_DATA_BEGIN/END])
- **fact source > derived memory**: FACT_SOURCE(project.json/outline/characters/world/rules/chapters)优先 DERIVED_MEMORY(memory/); 冲突以事实源为准, 可说"派生记忆可能已经过期"
- **all tools are explicit whitelist**: 未注册工具 → TOOL_NOT_FOUND; Agent 白名单外 → TOOL_PERMISSION_DENIED; 绝不 getattr/eval/exec/shell
- **tool args strict JSON**: validate_arguments(json object + string/integer/boolean + required + 未知字段拒绝); 坏参数 → INVALID_TOOL_ARGUMENTS 回填给模型修正
- **per-turn tool limits**: max_tool_calls_per_turn(默认 8)整个 turn 限制, 超限整批 preflight 拒绝(0 执行); max_tool_rounds(Chief=4)轮次上限; runaway 必然终止
- **weak-model fallback is bounded**: tool_calls=false 时不发 tools, 注入 bounded context pack(硬上限 10000 chars, 顺序: project metadata → chapter state → outline summary → current volume → memory index; 绝不塞 chapters/ 正文)
- **project chat does not mutate disk**: read-only byte invariant 测试保证
- **Tool output 截断**: 单次 4000 chars, [TRUNCATED total_chars=N], Unicode-safe
- **路径安全**: 所有工具路径经 ProjectStore.safe_path(resolve 后必须项目内, 防 ../绝对路径/symlink 逃逸); 错误消息不含绝对路径
- **Runtime 不依赖 CLI**: agents/ 禁止 argparse/print/input/sys.stdin/sys.stdout; 返回 AgentRunResult 结构化结果
- **Agent 会话仅内存**: 不落盘; 超限裁剪(保留开头 + 最近, 旧 tool 结果优先剪); system 动态注入不重复

## M4 Knowledge Workspace 稳定规则(稳定)
- **single mutation per LLM batch**: mutation 必须独占 response batch；mutation+read 或多个 mutation 全批 0 execute。
- **MutationService owns AI writes**: `update_outline/update_character/update_world/save_memory_entry` 的生产写入只能经过统一 mutation layer。
- **optimistic revision guard**: revision 是原始 bytes SHA256；新建为 `ABSENT`；stale 时拒绝覆盖并要求 re-read。
- **Snapshot is the only undo mechanism**: 复用 `.history/index.jsonl`，audit 不建第二套数据库；no-op 不产生 snapshot/history。
- **transaction order**: validate → safe_path → bytes/revision/no-op → prepare_snapshot → atomic write → history commit → final bytes verify；失败 restore/discard；失败的 rollback 报 `ROLLBACK_FAILED`。
- **FACT_SOURCE hierarchy**: outline/characters/world/rules/confirmed chapters 是事实源；memory 永远是 DERIVED_MEMORY，不能反向自动改正式设定。
- **Knowledge Doctor is read-only**: 只报告结构事实，不评分、不修复。
- **ContextCollector shared foundation**: M3 weak fallback 与 M5 Writer 共用 `core.context`；默认不收集章节正文。
- **Chief mutation boundary remains narrow**: Chief chat 不拥有 write chapter/confirm/reviewer/delete/shell 权限；正文写作只由 M5 workflow 调用 Writer。
- **Agent-facing reads are safe-path resolved**: exact lookup、H1 scan、search、CLI、doctor、context 与章节枚举在最终读取前均经过 `safe_path` 或 `safe_markdown_files`；项目外 symlink 不得泄漏内容。
- **memory revision contract**: `MEMORY_KINDS` 与 `memory_target_for_kind` 是 read/write 单一映射；Chief 保存前后必须 `read_memory`。
- **race hardening**: initial revision check + snapshot 后 writer 前 immediate byte recheck；race 时只 discard，绝不 restore 外部新内容。该语义是 optimistic concurrency，不是数据库 serializable transaction。
- **identity/no-op**: existing character/world 的 H1 必须稳定；no-op 是正常 `NO_CHANGE` 结果且不写 history。
- **project Chief routing**: tool_calls=true 时所有 project chat 使用完整 M4 registry；CLI 不做关键词/substring capability routing。tool_calls=false 永远无工具，并注入 `MUTATION_CAPABILITY: DISABLED`。

## M5 Writer 稳定规则(稳定)
- Writer 只输出正文且 AgentDef 工具列表为空；workflow 独占 canonical draft persistence。
- AI draft 固定 `origin=ai, status=draft`；M6 前不可 confirm，手动与 AI draft 路径保持分离。
- 每次 Writer 调用前必须构建 ContextBudget；默认永不自动注入全书正文。
- Chief Planner 同样必须按 chief model window 构建 bounded context；TaskCard JSON reserve 独立，首次无结果 `CONTEXT_TOO_LONG` 只缩到约 0.65 后重试一次，JSON repair 另限一次且不重带小说 context。
- Chief TaskCard 使用严格 JSON schema、一次 repair 与确定性 fallback；TaskCard/hash 不含 secret。
- 生成中内容只追加到 `drafts/.generation/`；完整或 length-truncated 后才 finalize canonical。
- interruption 保留非 canonical partial；空 partial 清理；resume 跨进程复用 TaskCard 并重建当前 context。
- partial sidecar 只保存恢复 allowlist 与 redacted `resume_card`，绝不保存 user instruction、Chief brief、prompt 或 full context；canonical provenance 保留原 task hash。
- rewrite/continue/resume 使用原始 bytes revision guard + Snapshot；外部/用户 draft 修改在 stale race 中获胜。
- canonical AI finalize、manual confirm 与 partial prepare 使用同一章级跨进程文件锁，封闭本应用并发 TOCTOU。
- Writer 首次无正文 `CONTEXT_TOO_LONG` 只缩 budget 重试一次；已有正文后绝不从头重试。
- `write --no-stream` 必须调用 Provider `chat()`，不是仅关闭 stdout delta；Provider 返回前失败不留下有效 partial。
- relevance source 由章纲、卷纲、结构化 TaskCard 与 instruction 共用构造，online workflow 与 offline `context plan` 保持一致。
- confirm 按 origin 分支：manual 允许 draft/user_confirmed；AI 只允许 ready，确认后保留 AI origin。M5 不提供 READY transition。
- Reviewer 仍属未来 M6：`DRAFT → REVIEWING → READY`，M6 NOT AUTHORIZED。

## M2 Provider 稳定规则(稳定)
- **Provider 抽象**: CLI → core/config → llm/factory → BaseProvider → OpenAICompatibleProvider → transport(httpx)。CLI 不知道 HTTP/SSE/JSON 细节; M3 Agent Runtime 只依赖 BaseProvider + llm/types 内部模型(ChatMessage/ChatResult/ChatChunk/Usage/ToolCall)
- **OpenAI-compatible first**: 只实现 openai_compatible; 未知 provider → UNSUPPORTED_PROVIDER(不偷偷兼容); 不绑定厂商 SDK(仅 httpx 通用 HTTP)
- **内部模型**: ChatResult 不携带裸 HTTP response; ToolCall.arguments_json 保留 JSON 字符串(M2 不执行); Usage.estimated_usage 标记 estimated
- **SecretStore only**: Key 只经 secret_store.get(reference); KEY_NOT_FOUND → KEY_NOT_CONFIGURED("API Key 尚未配置"); keyring 不可用 → 明确报错, 不 fallback 明文
- **safe errors**: ProviderError 统一错误码; message 安全(Key/URL query/raw body 绝不进入); 服务端错误体回显 Key → 替换 [REDACTED]; HTML/超长 body 截断, 不 dump
- **retry boundary**: 仅网络/timeout/5xx 最多重试 1 次(总尝试 ≤ 2); 400/401/403/404/429 不重试; 流式已产出任何 token → 不从头重试(STREAM_INTERRUPTED, 部分内容保留)
- **usage 隐私**: usage.jsonl 只记 metadata(tokens/model/duration), 绝不记 prompt/response/Key; 坏行跳过+warning(派生数据 ≠ history 严格性); usage 写失败不影响聊天成功
- **config validate 离线**: 绝不联网; test-provider 才真联网(最小 chat/completions 请求, 不 GET /models)
- **URL 安全**: 统一 validate_provider_base_url(urllib.parse.urlsplit)在**持久化 + 运行时**双边界强制: config set 写前 / config validate / factory / provider._endpoint 四入口复用; 仅 http(s)+host; 拒绝 userinfo/任何 query(含凭据参数, 大小写不敏感)/fragment/非 http(s); 完整 /chat/completions endpoint 原样使用, 末尾 / 不产生 //
- **credentials never belong in URL**: 拒绝时错误消息不含完整 URL(防回显 secret); Key 只能走 SecretStore
- **ChatChunk tool-call 字段是 delta**: tool_call_arguments_delta 每块只含本块增量(非累计); 聚合用 ToolCallAccumulator(按 index 独立); 内部 buffer 不伪装成 delta
- **HttpTransport lifecycle**: Provider.close() → transport.close()(释放 httpx.Client, 幂等); chat/test-provider 成功失败都走 finally close
- **流式**: SSE 逐行(data: 前缀/忽略空行注释/[DONE]); delta.content → text chunk; tool_calls 按 index 聚合; finish_reason 未知不崩溃; usage 可选, 缺失 → estimated
- **keyless**: secret_reference 空 → 不发送 Authorization(本地 Ollama); validate 给 warning 不 error
