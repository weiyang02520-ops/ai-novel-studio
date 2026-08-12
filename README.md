# AI Novel Studio

个人版 AI 小说创作软件 — 本地优先, 自带 API Key。

## 这是什么

一款面向个人小说作者的本地写作软件:

- 管理多本小说的项目结构(大纲/人物/世界观/章节/记忆)
- 章节本地持久化(纯 Markdown + JSON, 无数据库, 备份 = 拷贝目录)
- 未来: 主编 → Writer → Reviewer 多 Agent 辅助创作(当前未实现 AI)
- 所有 AI 调用走你自己的 OpenAI 兼容 API(自定义 Base URL / API Key / Model)
- 数据 100% 本地保存, 无云同步, 无账号系统

> 当前状态: M1 完成 — 小说项目 + 章节数据闭环可用; AI Provider 尚未实现(M2)。

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

### 配置模型(M2 才可用)

```bash
python -m adapters.cli config set default_model.base_url https://api.deepseek.com/v1
python -m adapters.cli config set default_model.model deepseek-chat
python -m adapters.cli config set-key deepseek-main   # 交互输入, 不回显
python -m adapters.cli config validate
```

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
- `config/settings.json` 只存 `secret_reference`, 不含真实 Key
- Key 绝不进入 Git / 日志 / 报错 / 交接文档

## 当前尚未实现

- AI Provider(模型调用)— M2
- 主编/Writer/Reviewer Agent — M3+
- 大纲/人物/世界观的 AI 辅助编辑
- 自动发布/手机端/会员 — 不在路线图

## 开发

```bash
python -m pytest tests/ -v    # 全量测试
```

架构: Core Engine(无 UI 依赖) + Adapter 层(CLI 现在, GUI 未来)。详见 `docs/context/AGENT_MEMORY.md`。
