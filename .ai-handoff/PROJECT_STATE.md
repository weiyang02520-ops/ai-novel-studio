# Project State (单一事实源)

> 本文件是 HANDOFF.md / STATUS.md 的唯一内容来源。
> Agent 每轮完成后**必须更新**本文件(阶段/完成/验证/下一步等), 再运行 checkpoint。
> 脚本每次 checkpoint 会基于本文件 + git 事实重写 HANDOFF.md 和 STATUS.md。

## project_goal

开发个人版 AI 小说创作软件(本地优先, 自带 API Key): 项目管理/章节/大纲/人物/世界观 + 主编→Writer→Reviewer 多 Agent 创作流程。设计依据: clean-room/ 设计规格(逆向仓库内), 从零写代码。

## phase

**M0 修复完成(第二次 PASS)**: 第一轮代码审核的 8 项修复全部落实, 30/30 测试通过。

## last_round

- M0 第一次外部代码审核通过(方向正确), 完成 8 项修复:
  1. 仓库可见性: API 实测为 public(创建时 private, 之后被改动) — 不擅自改, 待用户决定
  2. ConfigError: JSON 语法/根节点非 object/models 非 object/temperature/max_context_tokens 类型错误 → 人类可读错误 + exit 1, 无 traceback
  3. 默认配置合并: 空配置 save 后 reserve_output_tokens/max_recent_*/max_review_rounds/max_tool_calls_per_turn/max_snapshots/auto_accept 全部保留
  4. config set 白名单 + 类型转换(12 个字段), 未知字段拒绝, 敏感字段(api_key/token/password/secret/credential)拒绝
  5. SecretStore 错误语义: KEY_NOT_FOUND / BACKEND_UNAVAILABLE / BACKEND_ERROR; keyring 列入正式依赖, pytest 为 dev 依赖
  6. checkpoint 安全扫描: .env/.key/.pem/credentials/secrets 按路径级拦截(非内容匹配); 高风险二进制(exe/dll/7z/apk 等)默认阻止 push; 文本分块扫描(全文件, 非仅前 512KB)
  7. 交接状态同步(本文件)
  8. 全量 M0 验收重跑

## verified

- 单元测试 **30/30 PASS**(含非法 JSON/错误类型/未知字段/敏感拒绝/keyring 不可用/Key 不回显)
- CLI: --help / config validate(合法+非法+损坏 JSON)/ config set(白名单/类型/敏感)/ set-key(可用+不可用后端)全部真实运行
- SecretStore: Windows Credential Manager 真实读写(set/get/exists/delete)通过; keyring 不可用时清晰报错
- settings.json 无任何 Key(grep 验证)
- checkpoint: 敏感文件路径拦截(3 类真实文件测试通过)、远程 owner/repo 自动解析('weiyang02520-ops'/'ai-novel-studio')
- Core 无 argparse/click/终端 IO 依赖(静态检查)

## unverified

- 联网测试(M2 config test-provider 才做)
- M1+ 功能(项目/章节 CRUD)
- 真实 API Key 端到端对话(M2)

## architecture

```
ai-novel-studio/
├── core/          # Core Engine(无 UI 依赖): config.py 等
├── agents/        # Agent 定义(M3+)
├── llm/           # provider.py(M2+)/secret_store.py(已实现)
├── tools/         # 工具系统(M3+)
├── adapters/cli/  # CLI Adapter(M0-M7 测试入口, 唯一允许 argparse)
├── config/        # settings.json(非敏感)
├── data/          # 小说项目数据(本地)
├── tests/         # 单元测试(13 个)
└── scripts/       # ai_checkpoint.py(交接)
```

## known_issues

- **GitHub 仓库当前可见性为 public**(API 实测, 创建时曾为 private) — 若需私有请用户在 GitHub 网页端修改(Settings → Danger Zone → Change visibility), 我未擅自改动。
- Linux headless 无凭据服务时, SecretStore 写入会明确报错并建议环境变量(设计如此, 不降级明文)。

## key_decisions

- Core 与 Adapter 严格分离: Core 可被 Python 库方式调用, 换 GUI 不重写核心。
- 配置本地校验不联网(config validate); 联网测试留到 M2 config test-provider(HEAD 探测已废弃)。
- API Key 只进 SecretStore(系统凭据管理器), 环境变量为开发回退。
- config set 白名单制(未知/敏感字段拒绝), 类型严格转换。
- checkpoint 安全扫描: 敏感文件按路径拦截, 高风险二进制默认阻止, 文本分块扫描。
- 设计完全依据 clean-room/(逆向仓库), 不包含 XingLu 任何代码/资源。

## next_steps

- P0: 用户决定仓库可见性(private/public)。
- P0: ChatGPT 对 M0 修复做最终复核。
- P1: 复核通过后 M1 开发(新建小说、保存章节, 手动 confirm 路径)。

## review_focus

- 8 项修复是否全部落实(对应本文件 last_round)。
- ConfigError 覆盖是否完整(所有损坏配置路径)。
- 默认配置合并是否有遗漏。
- set 白名单是否过严/过松(models.<role> 动态匹配)。
- SecretStore 三错误码语义与 CLI 展示。
- checkpoint 安全扫描(路径拦截/二进制阻止/分块)是否有误报或漏报。

## critical_files

- core/config.py — 配置系统(ConfigError/默认合并/白名单)
- llm/secret_store.py — 密钥安全存储(三错误码)
- adapters/cli/main.py — CLI 入口(无 traceback)
- tests/test_m0.py — M0 测试(30 个)
- scripts/ai_checkpoint.py — 交接(remote 自动解析 + 安全扫描)

## recent_changes

- M0 第一轮代码审核 8 项修复全部完成, 30/30 测试通过(详见 last_round)。
