# AI Novel Studio

个人版 AI 小说创作软件 — 本地优先, 自带 API Key。

## 这是什么

一款面向个人小说作者的本地写作软件:

- 管理多本小说的项目结构(大纲/人物/世界观/章节/记忆)
- 章节本地持久化(纯 Markdown + JSON, 无数据库, 备份 = 拷贝目录)
- 直接对话: 支持符合 OpenAI Chat Completions 兼容接口的服务(OpenAI / DeepSeek / OpenRouter / Ollama 本地等)
- 主编 Agent 可规划章节并安全修改资料；Writer 生成受保护的 AI 草稿；Reviewer 提供 revision-bound 审稿关卡
- 所有 AI 调用走你自己的 OpenAI 兼容 API(自定义 Base URL / API Key / Model)
- 数据 100% 本地保存, 无云同步, 无账号系统

> 当前状态: M6 Reviewer 实现完成，等待外部验收。M7 的 Writer↔Reviewer 自动改写循环未授权、未实现。

## 安装

要求: Python 3.11+

```bash
pip install -e ".[dev]"
```

## 快速开始

### 创建小说

```bash
# 中文名自动生成稳定 ID
python -m adapters.cli novel create "山河不记" --genre 玄幻

# 显式指定 ID
python -m adapters.cli novel create "万象之旅" --id wanxiang-journey --genre 奇幻

python -m adapters.cli novel list
python -m adapters.cli novel show wanxiang-journey
python -m adapters.cli novel open wanxiang-journey
```

### 章节

```bash
# 创建草稿
python -m adapters.cli chapter write wanxiang-journey 1 --title "初入星城" --content "正文……"

# 长文本可用 --from-file
python -m adapters.cli chapter write wanxiang-journey 2 --title "夜市风波" --from-file chapter.txt

python -m adapters.cli chapter list wanxiang-journey
python -m adapters.cli chapter read wanxiang-journey 1 --draft

# 修改草稿(自动保留旧版快照)
python -m adapters.cli chapter update wanxiang-journey 1 --content "修改后的正文"

# 手动确认(草稿 → chapters/, 推进 current_chapter)
python -m adapters.cli chapter confirm wanxiang-journey 1
python -m adapters.cli chapter read wanxiang-journey 1

# 回滚最近一次 AI/修改快照
python -m adapters.cli history undo-last wanxiang-journey
```

### 配置模型并对话(M2)

支持**符合 OpenAI Chat Completions 兼容接口**的服务(兼容程度取决于服务实现)。
以下是一个通用模板, 替换为你的服务参数即可:

```bash
# 1. 配置服务地址 / 模型 / 密钥引用(引用名可自定义)
python -m adapters.cli config set default_model.base_url <BASE_URL>   # 例: https://api.deepseek.com/v1
python -m adapters.cli config set default_model.model <MODEL>         # 例: deepseek-chat
python -m adapters.cli config set default_model.secret_reference <REF> # 例: deepseek-main

# 2. 设置 API Key(交互输入, 不回显; 存入系统凭据管理器, 绝不进 settings.json)
python -m adapters.cli config set-key deepseek-main

# 3. 本地校验(不联网)
python -m adapters.cli config validate

# 4. 真实联网测试 Provider 连接(最小请求)
python -m adapters.cli config test-provider

# 5. 直接对话(默认流式输出)
python -m adapters.cli chat "你好, 简单介绍一下你自己"

# 6. 查看用量
python -m adapters.cli usage summary
python -m adapters.cli usage recent
```

常用变体:

```bash
# 非流式(完整响应后一次打印)
python -m adapters.cli chat "你好" --no-stream

# 指定角色模型 profile(未配置时自动回退 default_model)
python -m adapters.cli chat "你好" --role writer

# 附加 system 消息
python -m adapters.cli chat "你好" --system "你是一个简洁助手"

# 仅覆盖本次请求的温度(不写入配置文件)
python -m adapters.cli chat "你好" --temperature 0.2

# 密钥管理(不显示 Key 内容)
python -m adapters.cli config key-status deepseek-main
python -m adapters.cli config delete-key deepseek-main
```

无鉴权本地服务(如 Ollama / 本地 OpenAI-compatible server): `secret_reference` 留空即可,
请求不发送 Authorization 头(validate 会提示 keyless warning, 属预期)。

### 与主编对话(M4, grounded + safe editing)

```bash
# 直接问项目进度 — 主编会调用真实项目工具(list_chapters)后回答
python -m adapters.cli chat "我们的小说现在写到哪了？" --project my-novel

# 问大纲
python -m adapters.cli chat "第一卷大纲现在有哪些章节？" --project my-novel

# 显示工具调用 trace(不显示工具内容)
python -m adapters.cli chat "进度?" --project my-novel --show-tools

# 明确要求时，主编会先读 revision、写入、再读取复核
python -m adapters.cli chat "把第一卷目标改成找到北门钥匙。" --project my-novel --show-tools --show-diff
```

- **安全写入**: 只有明确的修改请求才写；现有文档必须先读，写入携带 raw-byte SHA256 revision。外部修改会触发 `STALE_REVISION`，不会被覆盖。
- **可撤销**: 每次成功 AI mutation 使用 `.history/` 快照，可用 `undo-last-change` 回滚。快照显著降低误写风险，但不承诺绝对不会丢失数据，重要项目仍应独立备份。
- **Grounding**: 主编不猜 — 涉及项目具体事实的问题必须先调用工具读取真实数据; 数据里没有 → 明确说"项目资料中没有找到"。事实源(project.json/大纲/人物/正文)优先于派生记忆(memory/)。
- **两种 chat 模式**: `chat "你好"`(无 `--project`)= M2 raw Provider 对话(诊断用);`chat "..." --project X` = 主编项目对话。
- **弱模型支持**: 模型配置 `tool_calls=false` 时, 不发送工具调用, 改为注入有限的项目数据包(有硬上限, 不塞全书正文)。
- 主编回答不自动保存; 多轮对话仅内存(关闭进程即消失)。

### 知识工作台与审计

```bash
python -m adapters.cli outline list my-novel
python -m adapters.cli outline show my-novel --volume 1
python -m adapters.cli outline status my-novel
python -m adapters.cli character list my-novel
python -m adapters.cli character search my-novel 林小满
python -m adapters.cli world show my-novel 北门
python -m adapters.cli memory search my-novel 钥匙
python -m adapters.cli rules show my-novel
python -m adapters.cli knowledge search my-novel 北门
python -m adapters.cli knowledge doctor my-novel
python -m adapters.cli knowledge revisions my-novel
python -m adapters.cli history list my-novel
python -m adapters.cli history show my-novel 1
python -m adapters.cli audit mutations my-novel
python -m adapters.cli undo-last-change my-novel
```

`knowledge doctor` 只报告重复 H1、history 损坏、章节冲突、symlink escape，以及 READY 草稿缺失/过期/损坏 review report 等事实，不会修复或改写项目。

- 所有 `chat --project` 在支持 tool calls 时都获得完整 M4 schema；CLI 不用关键词猜测能力。普通事实/建议问题仍只读，只有明确执行修改的语义才允许 Chief 选择 mutation。
- `tool_calls=false` 的弱模型永远只读：上下文显式标记 `MUTATION_CAPABILITY: DISABLED`，不发送或执行 mutation tools。
- 保存 memory 遵循 `read_memory(kind) → revision guarded append → read_memory(kind)`，并可通过 history undo。
- revision guard 是 optimistic concurrency，并在 snapshot 后、writer 前立即再次比较原始字节；它不声称具备数据库 serializable 隔离。
- `knowledge doctor <project> --json` 可输出机器可读的 `pass|warning|error` 与问题列表。

### 角色模型 profile(可选)

```bash
python -m adapters.cli config set models.writer.base_url <BASE_URL>
python -m adapters.cli config set models.writer.model <MODEL>
python -m adapters.cli config set models.writer.secret_reference <REF>
```

### Writer：从资料到 AI 草稿（M5）

先准备章细纲、人物、世界观与写作规则。可离线检查 Writer 实际会看到哪些资料；只有显式 `--show-text` 才输出完整渲染上下文。

```bash
# 1) 离线预算与来源清单（不调用 LLM）
python -m adapters.cli context plan my-novel --chapter 2

# 2) Chief 规划 + Writer 流式写作；默认写 current_chapter + 1
python -m adapters.cli write my-novel --instruction "沈砚第一次进入鹤梁山"

# 3) 查看 canonical AI draft
python -m adapters.cli draft list my-novel
python -m adapters.cli draft show my-novel 2

# 4) 对已有 AI draft 续写或完整改写
python -m adapters.cli write my-novel 2 --continue --instruction "写到发现契纹为止"
python -m adapters.cli write my-novel 2 --rewrite --instruction "开头更快进入冲突"

# 5) 中断后检查并跨进程恢复 partial
python -m adapters.cli draft partial list my-novel
python -m adapters.cli write my-novel 2 --resume

# 6) rewrite/continue 均进入现有 history，可撤销
python -m adapters.cli undo-last-change my-novel
```

`--show-plan` 展示 Chief TaskCard；`--plan-only` 只规划、不写项目；`--show-context` 只列 context manifest；`--no-stream` 会真正调用 Provider 的非流式 `chat()`，完整返回后才落入 partial/finalize。默认流式正文只追加到 `drafts/.generation/`，成功后才通过 revision guard + Snapshot 原子落入 canonical draft。

Chief Planner 与 Writer 都受模型窗口预算约束：Planner 使用较小的 JSON 输出预留，并在首次 `CONTEXT_TOO_LONG` 时把资料缩到约 65% 后只重试一次。partial sidecar 仅保存恢复所需的 redacted TaskCard，不保存用户 instruction、Chief brief 或项目全文。

M5 Writer 生成的 AI draft 不能直接 `chapter confirm`。确认边界按 origin 分开：manual 的 draft/user_confirmed 可确认；AI 草稿必须先通过 M6 Reviewer 进入 `ready`，并且 review report 必须仍与当前草稿 revision 精确匹配。

### Reviewer：从 AI 草稿到 READY（M6）

完整的人工作业边界是 `write → review → show → explicit confirm`：

```bash
# 1) Writer 创建 origin=ai, status=draft
python -m adapters.cli write my-novel 2 --instruction "沈砚进入鹤梁山"

# 2) Reviewer 审核当前 AI draft；不改正文、不自动调用 Writer
python -m adapters.cli review my-novel 2

# 3) 阅读结构化报告与问题
python -m adapters.cli review show my-novel 2
python -m adapters.cli review issues my-novel 2 --severity MAJOR

# 4a) PASS 只把草稿变成 READY；仍需用户显式确认
python -m adapters.cli chapter confirm my-novel 2

# 4b) NEEDS_WORK 保持 draft；用户决定是否手动改写并再次 review
python -m adapters.cli write my-novel 2 --rewrite --instruction "依据审稿意见收紧对白"
python -m adapters.cli review my-novel 2
```

`review --plan-only` 只做 Preflight、相关性与 ContextBudget 规划，不调用模型，也不修改状态、报告或 history。`review show/info/issues` 读取已保存的 `review/chNNNN.review.json`。READY 如需重新写作，先显式执行 `review reopen` 回到 draft。

Reviewer 采用方案 B：`reviewing` 只是进程内 Workflow run state，canonical frontmatter 不会持久停留在 `reviewing`，也没有 pending sidecar。进程失败时 canonical draft 保持不变；`review recover` 因此明确返回 `NO_PENDING_REVIEW`。

Reviewer 的质量判断是辅助意见，不是绝对真理：

- PASS 只表示在当前可见、严格有界的 Context 中，没有发现 BLOCKER 或 MAJOR；不表示文学质量客观满分。
- 重大问题应说明位置、证据、违反的事实/规则和修改建议；FACT_SOURCE 优先于 DERIVED_MEMORY。
- 它检查任务完成度、人物/世界/时间线/连续性、场景与因果逻辑、信息来源、重复与过度解释、AI 腔、对白、节奏、结尾职责和设定漂移。
- 它区分“读者暂时不知道”和作者逻辑漏洞，不把尚未解释的伏笔自动判错，也不为显得严格而硬找问题。
- Context 不足、草稿被截断、JSON 无法验证或任何确定性 BLOCKER 都 fail-closed：不能进入 READY。
- M6 不会自动 rewrite、continue、重复 review、confirm 或更新 post-confirm memory；这些编排属于尚未授权的 M7。

## 数据在哪里

```
data/novels/<project_id>/
├── project.json       # 项目元数据
├── outline/           # 大纲(梗概/卷/章细纲)
├── characters/        # 人物正式设定
├── world/             # 世界观
├── rules/             # 创作规则
├── chapters/          # 已确认章节(事实源)
├── drafts/            # 草稿
├── memory/            # 派生记忆(可重建)
├── review/            # 审稿记录
└── .history/          # 修改快照(undo 用)
```

`data/` 是运行数据, 不进入 Git。

## API Key 安全原则

- 真实 Key 只存系统凭据管理器(Windows Credential Manager / keyring), 环境变量为开发回退
- `config/settings.json` 只存 `secret_reference`, **不含真实 Key**
- Key 绝不进入 Git / 日志 / 报错 / usage 记录 / 交接文档
- 报错只显示安全摘要, 不 dump 原始响应体; TLS 校验默认开启
- 不把 Key 放进 Base URL 查询参数(config validate 会拒绝)

## 当前尚未实现

- Writer↔Reviewer 自动改写/多轮审稿/自动确认与 post-confirm memory 编排（M7，未授权）
- 自动摘要与更高级的长文本多级裁剪
- 聊天历史持久化 / 多轮会话(当前每轮独立请求; 主编多轮仅内存)
- 自动发布/手机端/会员 — 不在路线图

## 开发

```bash
python -m pytest tests/ -v    # 全量测试
```

架构: Core Engine(无 UI 依赖) + Adapter 层(CLI 现在, GUI 未来)。详见 `docs/context/AGENT_MEMORY.md`。
