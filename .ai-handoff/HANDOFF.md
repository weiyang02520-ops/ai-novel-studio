# Project Handoff

> 自动生成入口: `scripts/ai_checkpoint.py`
> 本文件在每次 Checkpoint 时由脚本**自动重写**, 内容来自单一事实源 `.ai-handoff/PROJECT_STATE.md` + git 事实。
> 请勿手改本文件, 需修改状态请改 PROJECT_STATE.md。

## 1. 项目目标

开发个人版 AI 小说创作软件(本地优先, 自带 API Key): 项目管理/章节/大纲/人物/世界观 + 主编→Writer→Reviewer 多 Agent 创作流程。设计依据: clean-room/ 设计规格(逆向仓库内), 从零写代码。

## 2. 当前阶段

**M1 TRANSACTION CLOSEOUT complete. Awaiting External ChatGPT final review.**
(M1 Gate 未授权; M2 NOT AUTHORIZED。)

## 3. 本轮完成内容

- **M1 TRANSACTION CLOSEOUT BATCH**(External ChatGPT CHANGES_REQUESTED 修复):
  1. **undo_last 真正 all-or-nothing**: preflight 一次性验证整个 changes(路径安全/previous/backup 存在可读/project.json backup 可解析)→ 保存 undo 前状态(仅本次文件)→ 应用 restore → 中途失败自动回滚到 undo 前字节状态 → 全部成功后才同步 Project.metadata + 移除 index record(deep equality 匹配防重复 seq 误删)。
  2. **confirm 无幽灵 history**: 新 Snapshot API(prepare/commit/discard/restore)— 先 prepare(backups 就位, index 未写)→ 业务全成功后才 commit history; 失败 → restore 业务文件 + discard backups。update_draft 同样事务化。
  3. **snapshot 半成品清理**: prepare 复制中途失败 → 已创建 backups 全部清理, index 无记录, 原文件不动, 后续 seq 不混乱。
  4. **诚实报错**: rollback 成功 → "已恢复到确认前状态"; rollback 本身失败(restore 尽力恢复全部 target 后仍失败)→ 高严重度 "确认失败且自动恢复未完整完成, 请运行 novel validate", 不声称已回滚, 不泄漏底层细节(原异常在 __cause__)。
  5. **history commit 失败一起回滚**: commit 原子写全量 index; commit 失败 → 业务文件一并恢复(无 history 的业务 commit 不发生)。
  6. **undo 幂等**: previous=absent 且目标不存在 → 跳过(record 移除失败后可安全重试)。
  7. **history list 显示 changes 摘要**(多文件显示 首target(+N))。

## 4. 本轮修改文件

- `M .ai-handoff/NEXT_TASKS.md`
- ` M .ai-handoff/PROJECT_STATE.md`
- ` M adapters/cli/commands.py`
- ` M core/chapter.py`
- ` M core/history.py`
- ` M docs/context/AGENT_MEMORY.md`
- `?? tests/test_transaction.py`

## 5. 已验证结果

- 单元测试 **176/176 PASS**(新增 14 个 closeout 测试: Case A 双 backup 缺失 0 修改 / Case B 中途写失败字节级回滚 / Case C unlink 失败回滚 / 成功 undo 完整断言(磁盘+内存+重启进程)/ 4 类失败 confirm 无历史无 orphan backup / snapshot 半成品清理+seq / update+confirm 的 history commit 失败回滚 / 诚实报错 2 例)。
- Acceptance(真实 CLI 临时项目): A Normal Flow PASS / B Confirm+Undo+重启进程 PASS / C Undo Preflight Failure(删 backup→undo 失败 exit=1, 0 修改, validate PASS, record 未消耗)PASS / D Undo Mid-Apply Failure PASS(单元测试 Case B/C 字节级回滚; CLI 无法注入 os.replace)/ E Failed Confirm Cleanup PASS(拒绝路径 exit=1 无 traceback, history 无记录, 0 backup)/ F Snapshot Partial Failure Cleanup PASS(单元测试)/ G Restart/Reopen PASS / H novel validate PASS。
- M0 regression: test_m0 + test_checkpoint **66/66 PASS**。
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
│   └── history.py     # Snapshot(prepare/commit/discard/restore) + undo-last
├── agents/        # (M3+)
├── llm/           # secret_store.py(已实现); provider.py(M2+)
├── tools/         # (M3+)
├── adapters/cli/  # CLI(main.py + commands.py: novel/chapter/history)
├── config/        # settings.json(非敏感)
├── data/novels/   # 运行数据(不入 Git)
├── docs/context/  # 长期记忆
├── tests/         # M0(66) + M1(96) + closeout(14)
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
- history 事务: prepare(backups, 不写 index)→ 业务 → commit(业务成功后); 失败 → restore(尽力全部)+ discard; 不引入 SQLite/WAL/框架。
- undo: preflight 全量验证 → capture 当前状态 → apply(幂等)→ 全成功才同步 metadata + 移除 record。
- ProjectStore(root) 注入: 生产 data/novels/, 测试 tmp_path。
- 中文名自动生成 novel-<hex> ID; 显示名保留原名。
- M1 不做 AI(Provider/Agent 全部 M2+)。

## 10. 下一步建议

- P0: 等待 External ChatGPT M1 final review(本轮 closeout)。
- P1: M2(Provider + config test-provider)仅在明确授权后开始。

## 11. 希望外部模型重点审查

- undo all-or-nothing: preflight 失败 0 修改 / 应用中途失败自动回滚 / 成功后才同步 metadata+移除 record。
- confirm 事务: 失败不留幽灵 history / 无 orphan backup / snapshot 半成品清理。
- history commit 失败: 业务文件一起回滚(无 history 的业务 commit 不发生)。
- 诚实报错: rollback 失败时不声称已回滚。

## 12. Git 信息

- Branch: main
- checkpoint_base_commit: 0ecae60cb966 ai-checkpoint: update 1 files
  (checkpoint 开始前的工作区 HEAD; 最新 checkpoint commit 以 GitHub 仓库 HEAD 为准)
- GitHub 仓库可见性: public(真实查询; 无法获取时显示 unknown)
- 最近 commit(本文件生成时): 0ecae60 2026-08-12 13:59:16 +0800
- 时间: 2026-08-12 15:06

## 13. Critical Files

- core/storage.py — 原子写/路径安全/ProjectStore
- core/project.py — 项目 CRUD/骨架/ID 安全
- core/chapter.py — 章节状态机/frontmatter/confirm(事务化)
- core/history.py — Snapshot API + undo-last(preflight/capture/apply)
- adapters/cli/commands.py — novel/chapter/history 命令
- tests/test_{project,chapter,storage,history,m1_cli,stabilization,transaction}.py

## 14. Recent Important Changes

- M1 TRANSACTION CLOSEOUT: undo all-or-nothing + confirm 无幽灵 history + snapshot 半成品清理 + 诚实报错, 176/176 测试通过, Acceptance A-H 全 PASS(详见 last_round)。
