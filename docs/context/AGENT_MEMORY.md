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
