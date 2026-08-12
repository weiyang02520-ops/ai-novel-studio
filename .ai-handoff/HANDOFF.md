# Project Handoff

> 自动生成入口: `scripts/ai_checkpoint.py`
> 本文件在每次 Checkpoint 时由脚本**自动重写**, 内容来自单一事实源 `.ai-handoff/PROJECT_STATE.md` + git 事实。
> 请勿手改本文件, 需修改状态请改 PROJECT_STATE.md。

## 1. 项目目标

开发个人版 AI 小说创作软件(本地优先, 自带 API Key): 项目管理/章节/大纲/人物/世界观 + 主编→Writer→Reviewer 多 Agent 创作流程。设计依据: clean-room/ 设计规格(逆向仓库内), 从零写代码。

## 2. 当前阶段

**M1 FINAL STABILIZATION complete. Awaiting External ChatGPT final review.**
(M1 Gate 未授权; M2 NOT AUTHORIZED。)

## 3. 本轮完成内容

- **M1 FINAL STABILIZATION BATCH**(数据一致性全面加固):
  1. **confirm 文件事务化**: draft+confirmed+project.json+history 四文件操作; 任何一步失败 → 完整 rollback(恢复旧 project.json/draft、删除新建 confirmed、恢复内存 metadata); draft 删除失败 = confirm 整体失败(不留下双份)。
  2. **history 完整 confirm undo**: 记录改为 changes 列表(project.json+draft+confirmed 三目标); undo-last 成组恢复(恢复 project.json/draft、删除 confirmed); 恢复后同步 Project 内存 metadata(不要求重开进程)。
  3. **history 严格 schema**: 非空坏行 → DataIntegrityError(不 silent skip); seq/operation/timestamp/changes 校验; _next_seq 拒绝损坏 index(防重复 seq)。
  4. **validate 跨文件检查**: duplicate(draft+confirmed 同编号)/ current_chapter==max(confirmed)/ 文件名-frontmatter 章节号一致。
  5. **list 不隐藏冲突**: 同编号多条 → 全部返回 + conflict 标记 + CONFLICT 状态(CLI 显示 [冲突!])。
  6. **metadata 严格类型**: current_chapter/current_volume/auto_accept/name/defaults 等驱动字段非法值(str/bool/float/负/null)→ DataIntegrityError, 不偷偷转换; property 直接返回。
  7. **update guards**: reviewing/ready 拒绝更新; origin=ai 拒绝(M1 manual 入口不绕过 AI 边界); user_confirmed 更新后回 draft。
  8. **frontmatter roundtrip**: characters 用 JSON 表示(list[str] 严格); title 含 CR/LF 拒绝(保证 Core 写出的数据 Core 能读回)。

## 4. 本轮修改文件

- `M .ai-handoff/PROJECT_STATE.md`
- ` M adapters/cli/commands.py`
- ` M core/chapter.py`
- ` M core/history.py`
- ` M core/project.py`
- ` M docs/context/AGENT_MEMORY.md`
- ` M tests/test_history.py`
- `?? tests/test_stabilization.py`

## 5. 已验证结果

- 单元测试 **162/162 PASS**(含 32 个稳定化测试: 故障注入 A/B/C/D / 崩溃残留 / metadata 损坏 9 例 / history 损坏 / roundtrip / guards)。
- Acceptance J(confirm undo): confirm→undo→重启进程→draft=v2/confirmed 无/current_chapter=0/validate PASS。
- Acceptance K(失败 rollback): 4 类故障注入(写 confirmed/写 project.json/删 draft/history 快照失败)→ 磁盘恢复 confirm 前状态(单元测试断言)。
- Acceptance L(跨文件): duplicate 检测 + list CONFLICT 显示 / metadata str 损坏报错 exit=1 无 traceback / current_chapter 不一致检测 — 全部 CLI 真实验证。
- M1 正常路径回归(create→write→reopen→update→undo→confirm→reopen)保持 PASS。
- Windows Credential Manager: REAL_ENV_CONFIRMED(前轮)。

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

- P0: 等待 External ChatGPT M1 final review。
- P1: M2(Provider + config test-provider)仅在明确授权后开始。

## 11. 希望外部模型重点审查

- confirm 事务: 故障注入 rollback 是否完整(4 类)。
- confirm undo: 成组恢复 + 内存同步 + 磁盘一致。
- validate 跨文件: duplicate / current_chapter / filename 一致性。
- metadata 严格类型: 所有损坏路径。
- history 严格 schema: 坏行/坏 seq/backup 缺失。
- frontmatter roundtrip: list + 换行拒绝。
- update guards: 状态/origin。

## 12. Git 信息

- Branch: main
- checkpoint_base_commit: 45a9505b5486 ai-checkpoint: add 12 new files
  (checkpoint 开始前的工作区 HEAD; 最新 checkpoint commit 以 GitHub 仓库 HEAD 为准)
- GitHub 仓库可见性: public(真实查询; 无法获取时显示 unknown)
- 最近 commit(本文件生成时): 45a9505 2026-08-12 13:29:27 +0800
- 时间: 2026-08-12 13:59

## 13. Critical Files

- core/storage.py — 原子写/路径安全/ProjectStore
- core/project.py — 项目 CRUD/骨架/ID 安全
- core/chapter.py — 章节状态机/frontmatter/confirm
- core/history.py — snapshot/undo
- adapters/cli/commands.py — novel/chapter/history 命令
- tests/test_{project,chapter,storage,history,m1_cli}.py — M1 测试(62)

## 14. Recent Important Changes

- M1 FINAL STABILIZATION: confirm 事务化 + history 完整 undo + validate 跨文件 + metadata 严格 + guards + frontmatter roundtrip, 162/162 测试通过, Acceptance J/K/L 全 PASS(详见 last_round)。
