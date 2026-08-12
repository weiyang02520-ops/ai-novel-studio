# Project Handoff

> 自动生成入口: `scripts/ai_checkpoint.py`
> 本文件在每次 Checkpoint 时由脚本**自动重写**, 内容来自单一事实源 `.ai-handoff/PROJECT_STATE.md` + git 事实。
> 请勿手改本文件, 需修改状态请改 PROJECT_STATE.md。

## 1. 项目目标

开发个人版 AI 小说创作软件(本地优先, 自带 API Key): 项目管理/章节/大纲/人物/世界观 + 主编→Writer→Reviewer 多 Agent 创作流程。设计依据: clean-room/ 设计规格(逆向仓库内), 从零写代码。

## 2. 当前阶段

**M0 final blocker fixes complete. Awaiting External ChatGPT final re-review.**
(M0 FINAL Gate 未授权; M1 NOT AUTHORIZED。)

## 3. 本轮完成内容

- 完成 M0 Final Gate Last Fix 的 4 项修复:
  1. **Null/Disabled keyring backend 正确识别(P0)**: `available` 改用官方 `keyring.core.recommended(backend)` 判定 — NullKeyring(禁用后端,get_password 静默返回 None 不抛异常)/FailKeyring(无可用后端)→ BACKEND_UNAVAILABLE;真实 Windows Credential Manager → available。不再用"get_password 没抛异常"判断。保留 NOVEL_DISABLE_KEYRING=1 测试钩子。
  2. **checkpoint 文件枚举 fail-closed(P0)**: 任一枚举命令(git diff --name-only -z / git diff --cached --name-only -z / git ls-files --others --exclude-standard -z)失败 → 抛 ScanFileCollectionError → security_scan 返回 BLOCK(SCAN_ERROR), 绝不"忽略失败继续扫描再 git add -A"(无法证明扫描集合 ≥ 提交集合)。
  3. **NUL 路径不 strip**: 新增专用 helper `run_nul()` — 直接读 bytes, 按 \\0 精确分割, 不做 strip/splitlines/quote 解析, 只去末尾空 segment。前导/尾随空格文件名精确保留。
  4. **chunk-boundary 扫描修复**: 文本分块扫描加 128 字符 carry buffer, 覆盖跨 64KB 边界拼接才完整的 secret("sk-" 在块尾 + 剩余在块头)。"扫描完整文件"不仅是扫描每个孤立 chunk。
- 新增测试: 真实 NullKeyring/FailKeyring 回归(available=False + get/set/delete/exists 全 BACKEND_UNAVAILABLE + Composite.set 不假成功 + value 不泄漏)、枚举失败 BLOCK、NUL 空格保留、跨块边界 secret BLOCK。

## 4. 本轮修改文件

- `M .ai-handoff/NEXT_TASKS.md`
- ` M .ai-handoff/PROJECT_STATE.md`
- ` M llm/secret_store.py`
- ` M scripts/ai_checkpoint.py`
- ` M tests/test_checkpoint.py`
- ` M tests/test_m0.py`

## 5. 已验证结果

- 单元测试 **66/66 PASS**(52 test_m0 + 14 test_checkpoint)。
- 真实 backend 探测: NullKeyring available=False / FailKeyring available=False / WinVault available=True / NOVEL_DISABLE_KEYRING=1 available=False(真实验证)。
- checkpoint: 枚举失败 fail-closed(测试)、NUL 路径空格保留(测试)、跨块 secret 拦截(测试)。
- CLI 全错误路径无 traceback(上轮验证仍有效)。
- **Windows Credential Manager 真实读写(set/get/exists/delete, 假 Key 测后删除): REAL_ENV_CONFIRMED**(本轮与上轮均实际执行)。

## 6. 未验证内容

- 联网测试(M2 config test-provider 才做)。
- M1+ 功能(项目/章节 CRUD)未实现。

## 7. 当前架构

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

## 8. 当前已知问题

- **GitHub 仓库当前可见性为 public**(API 实测, 创建时曾为 private) — 若需私有请用户在 GitHub 网页端修改, 我未擅自改动。这是事实记录, 不是本轮开发任务。
- Linux headless 无凭据服务时, SecretStore 写入会明确报错(BACKEND_UNAVAILABLE)并建议环境变量(设计如此, 不降级明文)。

## 9. 本轮关键决策

- checkpoint 扫描集合用 Git 机器可读输出(NUL-separated), 不解析人类可读 porcelain(避免目录折叠/转义/rename 解析错误)。
- set-key: TTY 强制隐藏输入, 失败即中止(安全性优先于便利性)。
- SecretStore 可用性: 探测真实后端(get_keyring + 空读), 不以"包可 import"为准。
- JSON load 严格类型 vs CLI 宽松转换: 两个入口分开, 不自动修正错误配置。
- 默认配置在 load 时 merge(用户覆盖默认), 保证 load 后立即完整。

## 10. 下一步建议

- P0: 等待 External ChatGPT re-review(M0 FINAL Gate)。
- P1: 仅在明确授权后进入 M1(NOT AUTHORIZED YET)。

## 11. 希望外部模型重点审查

- Null/Fail keyring backend 识别是否可靠(官方 recommended API)。
- fail-closed 枚举(任何 Git 命令失败必须 BLOCK)。
- NUL 路径精确保留(无 strip)。
- chunk-boundary 跨块 secret 检测。
- 上轮 7 项修复回归。

## 12. Git 信息

- Branch: main
- checkpoint_base_commit: 36904861c0b8 ai-checkpoint: update 1 files
  (checkpoint 开始前的工作区 HEAD; 最新 checkpoint commit 以 GitHub 仓库 HEAD 为准)
- GitHub 仓库可见性: public(真实查询; 无法获取时显示 unknown)
- 最近 commit(本文件生成时): 3690486 2026-08-12 12:47:14 +0800
- 时间: 2026-08-12 13:07

## 13. Critical Files

- core/config.py — 配置系统(严格类型/load merge/白名单)
- llm/secret_store.py — 密钥安全存储(三错误码/recommended 探测)
- adapters/cli/main.py — CLI 入口(TTY 安全 set-key, 无 traceback)
- scripts/ai_checkpoint.py — 交接(fail-closed 枚举/NUL/carry buffer)
- tests/test_m0.py — 配置/SecretStore/CLI 测试(52)
- tests/test_checkpoint.py — checkpoint 生产测试(14)

## 14. Recent Important Changes

- M0 Final Gate Last Fix 4 项修复完成, 66/66 测试通过(详见 last_round)。
