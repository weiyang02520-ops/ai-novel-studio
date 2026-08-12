# Project State (单一事实源)

> 本文件是 HANDOFF.md / STATUS.md 的唯一内容来源。
> Agent 每轮完成后**必须更新**本文件(阶段/完成/验证/下一步等), 再运行 checkpoint。
> 脚本每次 checkpoint 会基于本文件 + git 事实重写 HANDOFF.md 和 STATUS.md。

## project_goal

开发个人版 AI 小说创作软件(本地优先, 自带 API Key): 项目管理/章节/大纲/人物/世界观 + 主编→Writer→Reviewer 多 Agent 创作流程。设计依据: clean-room/ 设计规格(逆向仓库内), 从零写代码。

## phase

**M0 Final External Review fixes completed. Awaiting External ChatGPT re-review.**
(M0 FINAL Gate 未授权; M1 NOT AUTHORIZED。)

## last_round

- 完成 M0 Final Review 的 7 项修复:
  1. **checkpoint 安全扫描文件集合修复**: 弃用 porcelain 字符串解析, 改用 NUL-separated 三命令合并(git diff --name-only -z / git diff --cached --name-only -z / git ls-files --others --exclude-standard -z), 保证"扫描集合 ⊇ git add -A 提交集合"(修复: 新目录整体被 isfile 跳过但 add -A 会纳入)。
  2. **checkpoint 生产测试**: 新增 tests/test_checkpoint.py(11 用例, 临时 git 仓库真实模拟): 新目录/嵌套/敏感路径/secret 内容/rename/空格/Unicode/高风险二进制/大文本后半段 secret/图片白名单/三种 Git 状态/删除文件。
  3. **set-key TTY 安全**: TTY 环境只允许 getpass(隐藏输入), getpass 失败 → 明确报错 exit 1, 绝不 fallback input()(防 Key 显示在屏幕)。非 TTY(pipe/CI)允许 stdin 读取, stdout/stderr/异常不含 Key。
  4. **SecretStore 错误语义**: keyring 包可 import ≠ 系统凭据服务可用(新增可用性探测: get_keyring + 空读探测, NullKeyring/异常 → BACKEND_UNAVAILABLE); NOVEL_DISABLE_KEYRING=1 对 get/set/delete/exists 全路径一致; 错误消息不携带原始异常/真实 Key。
  5. **JSON 严格类型校验**(load 入口): temperature=true / temperature="0.8" / max_context_tokens=1.5 / max_context_tokens=true / tool_calls="true" / capabilities 非 object → ConfigError。CLI config set 的字符串转换独立保留(不同入口)。
  6. **load 时默认配置立即完整**: _from_dict 先 deep-merge DEFAULT_SETTINGS, load 部分配置后立即得到完整默认结构(不再依赖 save 补齐); 未知扩展字段保留。
  7. **交接文档修正**: NEXT_TASKS/PROJECT_STATE 反映真实 Gate 状态(不声称 PASS, 等待 External 复核)。

## verified

- 单元测试 **57/57 PASS**(46 test_m0 + 11 test_checkpoint)。
- checkpoint 文件集合: 新目录内嵌套文件/空格文件名/Unicode 文件名全部进入扫描集合(真实验证)。
- CLI 全错误路径无 traceback: 非法 JSON/root list/models string/capabilities 非 object/temperature bool/max_context float/未知字段/敏感字段/非法 int/SecretStore unavailable — 全部人类可读 + exit 码合理。
- SecretStore: NOVEL_DISABLE_KEYRING=1 时 get/set/delete/exists 一致返回 BACKEND_UNAVAILABLE(测试覆盖)。
- Core/Adapter 边界静态检查: core/ llm/ tools/ agents/ 无 argparse/click/终端 IO/CLI 依赖/GUI 框架; SecretStore 无终端交互。
- 依赖: 正式仅 keyring, dev 仅 pytest(未新增)。

## unverified

- Windows Credential Manager 真实读写验证(本轮将执行, 见下文 REAL_ENV)。
- 联网测试(M2 config test-provider 才做)。
- M1+ 功能(项目/章节 CRUD)未实现。

## architecture

```
ai-novel-studio/
├── core/          # Core Engine(无 UI 依赖): config.py
├── agents/        # Agent 定义(M3+, 空)
├── llm/           # secret_store.py(已实现); provider.py(M2+)
├── tools/         # 工具系统(M3+, 空)
├── adapters/cli/  # CLI Adapter(M0-M7 测试入口, 唯一允许 argparse)
├── config/        # settings.json(非敏感)
├── data/          # 小说项目数据(本地)
├── tests/         # test_m0.py(46) + test_checkpoint.py(11)
└── scripts/       # ai_checkpoint.py(交接, NUL-separated 扫描)
```

## known_issues

- **GitHub 仓库当前可见性为 public**(API 实测, 创建时曾为 private) — 若需私有请用户在 GitHub 网页端修改, 我未擅自改动。这是事实记录, 不是本轮开发任务。
- Linux headless 无凭据服务时, SecretStore 写入会明确报错(BACKEND_UNAVAILABLE)并建议环境变量(设计如此, 不降级明文)。

## key_decisions

- checkpoint 扫描集合用 Git 机器可读输出(NUL-separated), 不解析人类可读 porcelain(避免目录折叠/转义/rename 解析错误)。
- set-key: TTY 强制隐藏输入, 失败即中止(安全性优先于便利性)。
- SecretStore 可用性: 探测真实后端(get_keyring + 空读), 不以"包可 import"为准。
- JSON load 严格类型 vs CLI 宽松转换: 两个入口分开, 不自动修正错误配置。
- 默认配置在 load 时 merge(用户覆盖默认), 保证 load 后立即完整。

## next_steps

- P0: 等待 External ChatGPT re-review(M0 FINAL Gate)。
- P1: 仅在明确授权后进入 M1(NOT AUTHORIZED YET)。

## review_focus

- 7 项修复是否全部落实(对应 last_round)。
- checkpoint 文件集合是否满足"扫描集合 ⊇ 提交集合"(含 rename/新目录/空格/Unicode)。
- set-key TTY 安全(无 fallback input)。
- SecretStore 三错误码语义与 keyring 可用性探测。
- JSON 严格类型与 CLI 转换分离是否完整。
- load 时默认 merge 是否破坏未知扩展字段。

## critical_files

- core/config.py — 配置系统(严格类型/load merge/白名单)
- llm/secret_store.py — 密钥安全存储(三错误码/可用性探测)
- adapters/cli/main.py — CLI 入口(TTY 安全 set-key, 无 traceback)
- scripts/ai_checkpoint.py — 交接(NUL-separated 扫描集合)
- tests/test_m0.py — 配置/SecretStore/CLI 测试(46)
- tests/test_checkpoint.py — checkpoint 生产测试(11)

## recent_changes

- M0 Final Review 7 项修复全部完成, 57/57 测试通过(详见 last_round)。
