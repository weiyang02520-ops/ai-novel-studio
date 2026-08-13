# M7 Readiness — Interfaces Only

> M7 is **NOT AUTHORIZED**. 本文只记录 M6 已准备好的接口边界，不授权或实现任何 Writer↔Reviewer 自动循环。

## M6 已提供的接口

- `ReviewReport`：严格、可归一化、可哈希的结构化 verdict/issues 合约；`NEEDS_WORK` 问题可作为未来显式改写输入，但 M6 不自动转发给 Writer。
- `ReviewService`：revision-bound begin/finalize/reopen/inspect；独占 review artifact 与 draft/ready 状态持久化。
- Reviewer ContextBudget：按 reviewer model window 选择 draft、规则、章纲、相关人物/世界、最近 confirmed 等来源；不注入全书，截断时 fail-closed。
- READY boundary：只有 current matching PASS report 可支持 AI draft 保持 READY 并被用户显式确认。
- NEEDS_WORK feedback：结构化 category/severity/location/evidence/suggestion 已落入 `review/chNNNN.review.json`，草稿保持 draft。

## 未来 M7 可消费的边界

未来经单独授权后，M7 可以在这些接口之上设计 Writer↔Reviewer orchestration，例如把用户选择的 NEEDS_WORK 建议转成新的显式 Writer task，再对新 revision 发起 review。任何此类循环都必须继续遵守 revision、预算、history、正文所有权和显式用户确认边界。

## 当前明确不存在

- Reviewer 自动调用 Writer 或 DraftService rewrite
- 自动 continue、自动多轮 review 或 `max_review_rounds` 编排
- PASS 后自动 confirm/收编 confirmed
- 自动更新 post-confirm memory
- 持久化 reviewing/pending sidecar；M6 方案 B 的 `review recover` 返回 `NO_PENDING_REVIEW`

这些能力均属于 M7。**M7 NOT AUTHORIZED.**
