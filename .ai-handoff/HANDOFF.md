# Project Handoff

> 自动生成入口: `scripts/ai_checkpoint.py`
> 本文件在每次 Checkpoint 时由脚本**自动重写**, 内容来自单一事实源 `.ai-handoff/PROJECT_STATE.md` + git 事实。
> 请勿手改本文件, 需修改状态请改 PROJECT_STATE.md。

## 1. 项目目标

开发个人版 AI 小说创作软件(本地优先, 自带 API Key): 项目管理/章节/大纲/人物/世界观 + 主编→Writer→Reviewer 多 Agent 创作流程。设计依据: clean-room/ 设计规格(逆向仓库内), 从零写代码。

## 2. 当前阶段

**M2 FINAL CLOSEOUT complete. Awaiting External ChatGPT final review.**
(M2 未宣布 PASS; M3 NOT AUTHORIZED。)

## 3. 本轮完成内容

- **M2 FINAL CLOSEOUT BATCH**(External ChatGPT CHANGES_REQUESTED 修复):
  1. **Base URL 安全强制到生产路径**: 新增 `validate_provider_base_url()`(urllib.parse.urlsplit, 非 regex/startswith), 四入口复用 — config validate / config set base_url 写前 / factory.create_provider / provider._endpoint(运行时最后防线, 手工改 settings 也拦得住)。允许 http(s)+host(localhost/127.0.0.1)+完整 /chat/completions endpoint; 拒绝 空/仅 scheme/file/ftp/userinfo/任何 query(含 api_key/apikey/key/token/access_token/auth/authorization, 大小写不敏感)/fragment; 错误消息不含完整 URL(防回显 secret)。
  2. **config set 写前拒绝**: 危险 URL 在写入 settings.json 前抛 ConfigError; secret_reference 正常显示; 不建立"打印所有值"模式。
  3. **ToolCall delta 契约**: ChatChunk.tool_call_arguments 改名 **tool_call_arguments_delta**, 每块只含本块增量(不再输出累计值); 新增 ToolCallAccumulator(按 index 聚合, M3 复用); 测试全部改为 Consumer 方式拼接断言。
  4. **HttpTransport.close**: 真正释放 httpx.Client, 幂等可重复调用; Provider.close → transport.close 链路测试(成功/失败后均 close)。
  5. **测试**: 新增 test_provider_urls.py(允许 5 / 拒绝 16 / set 写前 / 运行时 / secret 不泄漏 5 类)+ 重写 stream tool_call 测试(delta 断言 4 个)+ close 测试 4 个 + CLI 更新; 全量 385/385。

## 4. 本轮修改文件

- `M .ai-handoff/NEXT_TASKS.md`
- ` M .ai-handoff/PROJECT_STATE.md`
- ` M core/config.py`
- ` M docs/context/AGENT_MEMORY.md`
- ` M llm/factory.py`
- ` M llm/openai_compatible.py`
- ` M llm/provider.py`
- ` M llm/transport.py`
- ` M llm/types.py`
- ` M tests/test_m2_cli.py`
- ` M tests/test_provider_http.py`
- ` M tests/test_provider_stream.py`
- `?? tests/test_provider_urls.py`

## 5. 已验证结果

- 单元测试 **385/385 PASS**(M2 CLOSEOUT 新增 71: URL 校验允许 5/拒绝 16/set 写前/运行时/secret 不泄漏、tool_call delta consumer 断言 4、close 生命周期 4、CLI 更新)。
- Acceptance(A-I, 真实 CLI + local mock): A test-provider 成功 PASS / B chat 流式拼接 PASS / C keyless 无 Authorization PASS / D 401 安全错误 PASS / E 503→200 retry 请求数 2 PASS / F 429 请求数 1 PASS / G stream interrupt 部分保留不重试 PASS / H usage summary PASS / I validate 离线(httpx.Client monkeypatch 断言)PASS。
- Secret 安全: 全局泄漏断言(401/403/500/network/malformed/chat CLI/test-provider/URL 拒绝路径 → stdout/stderr/exception/usage/settings 均无 fake secret)PASS。
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
- checkpoint_base_commit: d24e76d53e0b ai-checkpoint: add 18 new files
  (checkpoint 开始前的工作区 HEAD; 最新 checkpoint commit 以 GitHub 仓库 HEAD 为准)
- GitHub 仓库可见性: public(真实查询; 无法获取时显示 unknown)
- 最近 commit(本文件生成时): d24e76d 2026-08-12 16:36:06 +0800
- 时间: 2026-08-12 17:07

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

- M2 FINAL CLOSEOUT: URL 安全四入口强制(set 写前/validate/factory/endpoint)+ ToolCall delta 契约 + HttpTransport.close 生命周期, 385/385 测试通过(详见 last_round)。
- M2 COMPLETE: Provider 链路(httpx transport→OpenAI-compatible→BaseProvider)+ chat 流式/非流式 + config test-provider/delete-key/key-status + usage + 错误映射/重试/Key 安全, 314/314 测试通过, Acceptance A-I PASS, REAL_EXTERNAL=UNVERIFIED_MISSING_CONFIG。
- M1 TRANSACTION CLOSEOUT: undo all-or-nothing + confirm 无幽灵 history + snapshot 半成品清理 + 诚实报错, 176/176 测试通过, Acceptance A-H 全 PASS。
