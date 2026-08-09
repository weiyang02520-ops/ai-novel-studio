# Project State (单一事实源)

> 本文件是 HANDOFF.md / STATUS.md 的唯一内容来源。
> Agent 每轮完成后**必须更新**本文件(阶段/完成/验证/下一步等), 再运行 checkpoint。
> 脚本每次 checkpoint 会基于本文件 + git 事实重写 HANDOFF.md 和 STATUS.md。

## project_goal

开发个人版 AI 小说创作软件(本地优先, 自带 API Key): 项目管理/章节/大纲/人物/世界观 + 主编→Writer→Reviewer 多 Agent 创作流程。设计依据: clean-room/ 设计规格(逆向仓库内), 从零写代码。

## phase

**M0 完成(PASS)**: 工程骨架 + Core/CLI 分离 + 配置系统 + SecretStore 可运行 + CLI 可启动。

## last_round

- M0 开发: 建立独立工程骨架(core/agents/llm/tools/adapters), Core 与 CLI Adapter 分离, Core 无 argparse/终端依赖。
- 配置系统: settings.json(非敏感)+ 本地校验 validate(不联网)。
- SecretStore: keyring(Windows Credential Manager)读写实测通过, 环境变量回退, 真实 Key 不入文件。
- CLI: `python -m adapters.cli --help` / `config validate` / `config show` / `config set` / `config set-key`。
- 单元测试 13/13 通过。

## verified

- `python -m adapters.cli --help` → 输出命令帮助, exit 0 ✅
- `python -m adapters.cli config validate`(合法配置)→ "配置有效(本地校验通过, 未联网)" ✅
- 配置非法(base_url 空)→ 明确错误 + exit 1 ✅
- SecretStore 真实读写(Windows Credential Manager): set/get/exists/delete 全部通过 ✅
- settings.json 无真实 Key(仅 secret_reference 结构)✅
- 单元测试 13/13 PASS ✅
- Core 层静态检查无 argparse/click/sys.stdin/stdout 依赖 ✅

## unverified

- 未联网测试(联网仅 M2 config test-provider)。
- M1+ 功能未实现(项目/章节 CRUD)。
- 真实 API Key 端到端对话未验证(M2)。

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

- keyring 依赖已安装(Windows Credential Manager WinVaultKeyring)。
- Linux headless 无凭据服务时, SecretStore 写入会明确报错并建议环境变量(设计如此, 不降级明文)。

## key_decisions

- Core 与 Adapter 严格分离: Core 可被 Python 库方式调用, 换 GUI 不重写核心。
- 配置本地校验不联网(config validate); 联网测试留到 M2 config test-provider(HEAD 探测已废弃)。
- API Key 只进 SecretStore(系统凭据管理器), 环境变量为开发回退。
- 设计完全依据 clean-room/(逆向仓库), 不包含 XingLu 任何代码/资源。

## next_steps

- P0: 建立 GitHub 私有仓库, checkpoint + push。
- P0: 外部模型第一次代码审核。
- P1: M1 开发(新建小说、保存章节, 手动 confirm 路径)。

## review_focus

- Core/Adapter 分层是否干净。
- SecretStore 安全(Key 是否可能泄露到文件/日志)。
- 配置校验逻辑与错误消息质量。
- 测试覆盖是否足够(M0 边界)。

## critical_files

- core/config.py — 配置系统(核心)
- llm/secret_store.py — 密钥安全存储(核心)
- adapters/cli/main.py — CLI 入口
- tests/test_m0.py — M0 测试
- scripts/ai_checkpoint.py — 交接(remote 自动解析)

## recent_changes

- (首轮: M0 全部代码)
