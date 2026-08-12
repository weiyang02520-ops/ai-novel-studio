# Project Handoff

> 自动生成入口: `scripts/ai_checkpoint.py`
> 本文件在每次 Checkpoint 时由脚本**自动重写**, 内容来自单一事实源 `.ai-handoff/PROJECT_STATE.md` + git 事实。
> 请勿手改本文件, 需修改状态请改 PROJECT_STATE.md。

## 1. 项目目标

开发个人版 AI 小说创作软件(本地优先, 自带 API Key): 项目管理/章节/大纲/人物/世界观 + 主编→Writer→Reviewer 多 Agent 创作流程。设计依据: clean-room/ 设计规格(逆向仓库内), 从零写代码。

## 2. 当前阶段

**M1 implementation complete. Awaiting External ChatGPT M1 review.**
(M1 Gate 未授权; M2 NOT AUTHORIZED。)

## 3. 本轮完成内容

- **M1 COMPLETE BATCH(小说项目 + 章节本地持久化)**:
  - Core 新增 4 文件: storage.py(原子写/路径安全/ProjectStore)/ project.py(项目 CRUD/目录骨架/ID 安全)/ chapter.py(frontmatter/章节状态机/confirm)/ history.py(snapshot/undo-last)
  - CLI 新增 novel/chapter/history 子命令(commands.py)
  - 章节: DRAFT → USER_CONFIRMED → CONFIRMED(手动路径); confirm 后 current_chapter=max 推进; confirmed 正文受保护
  - .history 快照: update 与 confirm 的 project.json 修改自动快照; undo-last 恢复
  - 自研有限 frontmatter(不引入 YAML 依赖); UTF-8 全流程; 原子写(same-dir temp + os.replace)
  - 数据闭环验收 A-I 全部 PASS(创建→草稿→重开→更新→undo→confirm→最终重开→隔离→损坏检测)
  - 新增测试 62 个(test_project 21 / test_chapter 22 / test_storage 7 / test_history 7 / test_m1_cli 5)
  - 新增 docs/context/CHATGPT_MEMORY.md + AGENT_MEMORY.md + README.md

## 4. 本轮修改文件

- `M .ai-handoff/NEXT_TASKS.md`
- ` M .ai-handoff/PROJECT_STATE.md`
- ` M adapters/cli/main.py`
- `?? README.md`
- `?? adapters/cli/commands.py`
- `?? core/chapter.py`
- `?? core/history.py`
- `?? core/project.py`
- `?? core/storage.py`
- `?? docs/`
- `?? tests/test_chapter.py`
- `?? tests/test_history.py`
- `?? tests/test_m1_cli.py`
- `?? tests/test_project.py`
- `?? tests/test_storage.py`

## 5. 已验证结果

- 单元测试 **128/128 PASS**(M0 66 + M1 62)。
- Acceptance A-I 真实 CLI 验收全部 PASS(临时数据目录, 已清理)。
- M0 回归: Config/SecretStore/checkpoint 安全/set-key 全部保持通过。
- 章节数据闭环: 写入→重开→读回 UTF-8 完全一致; current_chapter 只随 confirm 推进; 旧章节 confirm 不倒退; confirmed 保护; 多小说隔离; 损坏检测无 traceback。
- Windows Credential Manager: REAL_ENV_CONFIRMED(前轮)。
- Core/Adapter 边界: 新增 core 文件无 argparse/终端 IO。

## 6. 未验证内容

- 联网测试(M2 config test-provider 才做)。
- AI Provider / Agent(M2+/M3+)未实现。

## 7. 当前架构

```
ai-novel-studio/
├── core/          # Core Engine(无 UI 依赖)
│   ├── config.py      # 配置系统
│   ├── storage.py     # 原子写 + 路径安全 + ProjectStore
│   ├── project.py     # 项目 CRUD + 目录骨架 + ID 安全
│   ├── chapter.py     # 章节 frontmatter + 状态机 + confirm
│   └── history.py     # snapshot / undo-last
├── agents/        # (M3+)
├── llm/           # secret_store.py(已实现); provider.py(M2+)
├── tools/         # (M3+)
├── adapters/cli/  # CLI(main.py + commands.py: novel/chapter/history)
├── config/        # settings.json(非敏感)
├── data/novels/   # 运行数据(不入 Git)
├── docs/context/  # 长期记忆
├── tests/         # M0(66) + M1(62)
└── scripts/       # ai_checkpoint.py
```

## 8. 当前已知问题

- **GitHub 仓库当前可见性为 public**(API 实测, 创建时曾为 private) — 若需私有请用户在 GitHub 网页端修改, 我未擅自改动。这是事实记录, 不是开发任务。
- Linux headless 无凭据服务时, SecretStore 写入会明确报错(BACKEND_UNAVAILABLE)并建议环境变量(设计如此, 不降级明文)。

## 9. 本轮关键决策

- M1 用最小合理结构: storage/project/chapter/history 4 个 Core 文件(不拆十几个类)。
- 自研有限 frontmatter(不引入 YAML 依赖, 只解析/写出自己生成的格式, round-trip 稳定)。
- 原子写: same-dir temp + flush + os.replace; 失败清理临时文件不破坏原文件。
- 章节状态机手动路径: DRAFT → USER_CONFIRMED → CONFIRMED; current_chapter=max 推进不倒退。
- .history: 对"已有内容修改"前快照(update/confirm 的 project.json); undo-last 顺序回滚。
- ProjectStore(root) 注入: 生产 data/novels/, 测试 tmp_path。
- 中文名自动生成 novel-<hex> ID; 显示名保留原名。
- M1 不做 AI(Provider/Agent 全部 M2+)。

## 10. 下一步建议

- P0: 等待 External ChatGPT M1 review。
- P1: M2(Provider + config test-provider)仅在明确授权后开始。

## 11. 希望外部模型重点审查

- 数据闭环是否可靠(重开/UTF-8/状态机/current_chapter)。
- confirmed 保护 / 路径安全 / 损坏检测是否无口。
- .history snapshot 与 undo 是否真实可用(非摆设)。
- 多小说隔离。
- M0 回归。

## 12. Git 信息

- Branch: main
- checkpoint_base_commit: 87f229752264 ai-checkpoint: update 1 files
  (checkpoint 开始前的工作区 HEAD; 最新 checkpoint commit 以 GitHub 仓库 HEAD 为准)
- GitHub 仓库可见性: public(真实查询; 无法获取时显示 unknown)
- 最近 commit(本文件生成时): 87f2297 2026-08-12 13:07:01 +0800
- 时间: 2026-08-12 13:29

## 13. Critical Files

- core/storage.py — 原子写/路径安全/ProjectStore
- core/project.py — 项目 CRUD/骨架/ID 安全
- core/chapter.py — 章节状态机/frontmatter/confirm
- core/history.py — snapshot/undo
- adapters/cli/commands.py — novel/chapter/history 命令
- tests/test_{project,chapter,storage,history,m1_cli}.py — M1 测试(62)

## 14. Recent Important Changes

- M1 COMPLETE BATCH: 小说项目 + 章节数据闭环完成, 128/128 测试通过, Acceptance A-I 全 PASS(详见 last_round)。
