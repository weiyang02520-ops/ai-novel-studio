# Project Handoff

> 自动生成入口: `scripts/ai_checkpoint.py`
> 本文件在每次 Checkpoint 时由脚本**自动重写**, 内容来自单一事实源 `.ai-handoff/PROJECT_STATE.md` + git 事实。
> 请勿手改本文件, 需修改状态请改 PROJECT_STATE.md。

## 1. 项目目标

开发个人版 AI 小说创作软件(本地优先, 自带 API Key): 项目管理/章节/大纲/人物/世界观 + 主编→Writer→Reviewer 多 Agent 创作流程。设计依据: clean-room/ 设计规格(逆向仓库内), 从零写代码。

## 2. 当前阶段

**M2 implementation complete. Awaiting External ChatGPT M2 review.**
(M2 未宣布 PASS; M3 NOT AUTHORIZED。)

## 3. 本轮完成内容

- **M2 COMPLETE MEGA BATCH**(Provider + 真实 API + Streaming + Chat + Usage + Diagnostics):
  1. **llm/ 模块**: types.py(内部统一模型 ChatMessage/ToolCall/Usage/ChatResult/ChatChunk)、provider.py(BaseProvider + 15 种错误码 + estimate_tokens)、transport.py(httpx 封装: connect/read 超时分离、follow_redirects=False、verify=True、SSEStream 异常包装)、openai_compatible.py(端点拼接/防 Key 查询参数/SecretStore 解析/非流式+SSE 解析/状态映射/重试)、factory.py(仅 openai_compatible, 未知 provider 拒绝)、usage.py(JSONL metadata-only)、testing.py(FakeProvider/FakeTransport 供 M3 复用)。
  2. **错误映射**: 400(模型不存在/context 超长分类)/401/403/404/405(端点提示)/408(可重试)/409/422/429(Retry-After 提示)/5xx(可重试 1 次)/418 等未知 → HTTP_ERROR; HTML 错误页安全消息; body 截断 600 chars; 服务端回显 Key → [REDACTED]。
  3. **重试边界**: 网络/timeout/5xx 最多 1 次(总 ≤2); 400/401/403/404/429 不重试; 流式已产出 → STREAM_INTERRUPTED 不从头重试。
  4. **CLI**: config test-provider(--role, 真联网最小 chat/completions)/delete-key/key-status(不显示 Key)/config show 增强(key: configured/missing/unknown)/chat(--role/--system/--no-stream/--temperature 校验/默认流式/空输入拒绝/超长本地保护/Ctrl+C→130)/usage summary+recent; 全部无 traceback。
  5. **usage**: data/logs/usage.jsonl(可注入路径), 只记 metadata, 坏行跳过+warning, 写失败不阻断聊天成功。
  6. **依赖**: pyproject.toml 新增 httpx>=0.27(通用 HTTP: timeout/streaming/SSE/testability; 无厂商 SDK)。
  7. **测试**: 新增 6 个文件 + local mock server(ThreadingHTTPServer, localhost): 单测(FakeTransport: 解析/状态/重试/泄漏)、集成(真实 HttpTransport→mock: non-stream/stream/auth/keyless/retry/interrupt)、CLI subprocess(全命令 + 全局 Secret 不泄漏断言)。

## 4. 本轮修改文件

- `M .ai-handoff/NEXT_TASKS.md`
- ` M .ai-handoff/PROJECT_STATE.md`
- ` M README.md`
- ` M adapters/cli/main.py`
- ` M core/config.py`
- ` M docs/context/AGENT_MEMORY.md`
- ` M pyproject.toml`
- `?? adapters/cli/m2.py`
- `?? llm/factory.py`
- `?? llm/openai_compatible.py`
- `?? llm/provider.py`
- `?? llm/testing.py`
- `?? llm/transport.py`
- `?? llm/types.py`
- `?? llm/usage.py`
- `?? scripts/m2_demo_mock.py`
- `?? tests/conftest.py`
- `?? tests/mock_server.py`
- `?? tests/test_m2_cli.py`
- `?? tests/test_m2_integration.py`
- `?? tests/test_provider_errors.py`
- `?? tests/test_provider_http.py`
- `?? tests/test_provider_stream.py`
- `?? tests/test_provider_types.py`
- `?? tests/test_usage.py`

## 5. 已验证结果

- 单元测试 **314/314 PASS**(新增 138 个 M2 测试: types 13 / http 41 / stream 16 / errors 14 / usage 9 / 集成 13 / CLI 21 + 4 个既有修复回归)。
- Acceptance(A-I, 真实 CLI + local mock): A test-provider 成功 PASS / B chat 流式拼接 AI Novel Studio PASS / C keyless 无 Authorization PASS / D 401 安全错误无 Key 无 traceback PASS / E 503→200 retry 请求数 2 PASS / F 429 请求数 1 PASS / G stream interrupt 部分保留不重试 PASS / H usage summary requests 正确 PASS / I validate 离线(httpx.Client monkeypatch 断言不联网)PASS。
- Secret 安全: 全局泄漏断言(401/403/500/network/malformed/chat CLI/test-provider → stdout/stderr/exception/usage 文件/settings 均无 fake secret)PASS。
- M0 regression: test_m0 + test_checkpoint **66/66 PASS**; M1 regression: novel/chapter/history/transaction 全部 PASS。
- Windows Credential Manager: REAL_ENV_CONFIRMED(前轮)。

## 6. 未验证内容

- 真实外部 Provider 成功调用(生产配置缺 secret_reference; 用户配置 Key 后可 test-provider 验证)。
- AI Agent(Runtime/主编/Writer/Reviewer, M3+)未实现。

## 7. 当前架构

```
ai-novel-studio/
├── core/          # Core Engine(无 UI 依赖)
│   ├── config.py      # 配置系统(SUPPORTED_PROVIDERS + 离线 validate)
│   ├── storage.py     # 原子写 + 路径安全 + ProjectStore
│   ├── project.py     # 项目 CRUD + 目录骨架 + ID 安全
│   ├── chapter.py     # 章节 frontmatter + 状态机 + confirm
│   └── history.py     # Snapshot(prepare/commit/discard/restore) + undo-last
├── llm/           # Provider 层(无 UI 依赖)
│   ├── secret_store.py    # 密钥安全存储(已实现)
│   ├── types.py           # 内部统一数据模型
│   ├── provider.py        # BaseProvider + ProviderError(15 错误码)
│   ├── transport.py       # httpx 封装(超时/SSE/安全默认)
│   ├── openai_compatible.py # OpenAI Chat Completions 兼容 Provider
│   ├── factory.py         # config + SecretStore → Provider
│   ├── usage.py           # 本地 usage JSONL(metadata-only)
│   └── testing.py         # FakeProvider/FakeTransport(M3 复用)
├── adapters/cli/  # CLI(main.py + commands.py + m2.py)
├── config/        # settings.json(非敏感; 用户运行状态, 不入 Git)
├── data/          # 运行数据: novels/ + logs/usage.jsonl(不入 Git)
├── docs/context/  # 长期记忆
├── tests/         # M0(66) + M1(96+14) + M2(138)
└── scripts/       # ai_checkpoint.py + m2_demo_mock.py(本地演示)
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
- chat 是临时单轮入口: 不持久化会话、不写小说数据(避免 M2/M3 边界混乱)。

## 10. 下一步建议

- P0: 等待 External ChatGPT M2 review。
- P1: M3(Agent Runtime + Chief Editor)仅在明确授权后开始。

## 11. 希望外部模型重点审查

- Provider 与 CLI 解耦: CLI 不接触 HTTP/SSE/JSON 细节; M3 Agent Runtime 可复用 BaseProvider + llm/types。
- Key 安全: ProviderError/CLI/usage/settings 全路径不泄漏; 服务端回显 Key → [REDACTED]。
- retry 边界: 网络/timeout/5xx ≤1 次; 401/429 不重试; 流式已产出不重试。
- config validate 绝对离线; test-provider 真联网(最小 chat/completions)。
- 错误映射完整性: 400 分类 / HTML / 截断 / 未知状态码。
- M1 transaction 回归(undo all-or-nothing / confirm 无幽灵 history)。

## 12. Git 信息

- Branch: main
- checkpoint_base_commit: 80253ae46585 ai-checkpoint: update 1 files
  (checkpoint 开始前的工作区 HEAD; 最新 checkpoint commit 以 GitHub 仓库 HEAD 为准)
- GitHub 仓库可见性: public(真实查询; 无法获取时显示 unknown)
- 最近 commit(本文件生成时): 80253ae 2026-08-12 15:06:35 +0800
- 时间: 2026-08-12 16:36

## 13. Critical Files

- core/storage.py — 原子写/路径安全/ProjectStore
- core/project.py — 项目 CRUD/骨架/ID 安全
- core/chapter.py — 章节状态机/frontmatter/confirm(事务化)
- core/history.py — Snapshot API + undo-last(preflight/capture/apply)
- core/config.py — ModelConfig/Settings/离线 validate/SUPPORTED_PROVIDERS
- llm/types.py — 内部统一数据模型(M3 依赖)
- llm/openai_compatible.py — Provider(非流式/SSE/错误映射/重试)
- llm/transport.py — httpx 封装
- llm/usage.py — usage JSONL
- adapters/cli/{main,m2,commands}.py — CLI 三块
- tests/ — M0(66) + M1(110) + M2(138)

## 14. Recent Important Changes

- M2 COMPLETE: Provider 链路(httpx transport→OpenAI-compatible→BaseProvider)+ chat 流式/非流式 + config test-provider/delete-key/key-status + usage + 错误映射/重试/Key 安全, 314/314 测试通过, Acceptance A-I PASS, REAL_EXTERNAL=UNVERIFIED_MISSING_CONFIG(详见 last_round)。
- M1 TRANSACTION CLOSEOUT: undo all-or-nothing + confirm 无幽灵 history + snapshot 半成品清理 + 诚实报错, 176/176 测试通过, Acceptance A-H 全 PASS。
