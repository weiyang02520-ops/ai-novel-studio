# M8 Readiness — Potential Capabilities Only

> M8 is **NOT AUTHORIZED / NOT STARTED**. 本文只记录 M7 完成后暴露的稳定边界和未来候选，不授权实现任何能力。

## M7 已提供的稳定基础

- `CreationWorkflow` 已把 Chief、Writer、Reviewer 编排成有界、可恢复、revision-safe 的章节创作流程。
- READY 仍是用户确认前状态；只有 current matching PASS artifact 才可由用户显式 `chapter confirm`。
- `workflow/.runs/` 是最小 orchestration metadata，不是小说事实源、会话数据库或第二套 history。
- confirmed 正文、`current_chapter` 与 outline/characters/world/rules/memory 在 compose 期间保持不变。
- Usage 仅聚合 tokens/model/duration 等 metadata，不保存 Prompt、Context、正文或反馈文本。

## 未来可单独评审的 M8 候选

- 用户显式 confirm 后的 memory extraction、摘要与长期一致性更新。
- 长会话持久化、上下文 compaction 与可解释的恢复策略。
- GUI / application layer，以及 compose 阶段和人工 Gate 的可视化。
- 项目导入、导出、迁移与可恢复备份流程。
- 质量趋势与成本分析；只使用必要 metadata，不建立未经授权的正文遥测。

## 继续适用的硬边界

- 不自动 confirm，不替用户收编章节。
- 不让 Reviewer 写正文，不让 Writer 或 review feedback 改正式事实源。
- 不因 GUI、analytics 或 session persistence 绕过 revision CAS、Snapshot/history、ContextBudget、SecretStore 或显式用户 Gate。
- 不把 compose sidecar 扩展成隐含数据库、undo system、账号/云同步层。
- 不自动引入 Plugin、MCP、vector DB、cloud、multi-user 或 collaboration 架构。

## Gate

**M8 NOT AUTHORIZED.**

任何 M8 工作必须先获得独立授权、设计评审和新的验收标准。
