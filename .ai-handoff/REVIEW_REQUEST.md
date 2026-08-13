# External Review Request — M6 Reviewer Super Batch

请审核 M6 Reviewer 实现。M6 implementation complete，正在等待 External ChatGPT M6 review；**M7 NOT AUTHORIZED**。

## 重点路径

- `agents/review_report.py`, `agents/reviewer.py`, `agents/prompts/reviewer_system.md`
- `core/review_context.py`, `core/review_preflight.py`, `core/context_budget.py`
- `core/review.py`, `core/review_workflow.py`, `core/chapter.py`, `core/knowledge.py`
- `adapters/cli/m6.py`, `adapters/cli/main.py`
- `tests/test_m6_*.py`

## Gate Matrix

- Reviewer 无工具、不依赖具体 HTTP Provider、不调用 Writer、不修改正文
- strict ReviewReport、一次 schema-only repair、malformed/double malformed fail-closed
- production messages strict model-window budget 与一次 0.65 overflow retry
- bounded grounded context；不注入全书；任何 draft truncation 不能证明 PASS
- deterministic Preflight issue 不能被模型 PASS 擦除
- 方案 B：无持久 reviewing/pending；recover=`NO_PENDING_REVIEW`
- draft/report exact-revision CAS、external race 零覆盖、transaction rollback/undo
- PASS→ready、NEEDS_WORK→draft；正文 body byte-identical
- READY 必须有 current matching strict PASS；confirm 仍由用户显式执行
- review CLI、Doctor stale/malformed/symlink、localhost HTTP E2E、privacy/static/M0–M5 regression

请重点尝试 malformed report、tiny context、retry 后 draft truncation、draft/report races、symlink path、transaction fault、READY stale report，以及任何可能自动调用 Writer/confirm 的越权路径。

M7 自动 rewrite/continue、多轮 review、自动 confirm、post-confirm memory 均不在本次实现中。

## Verification

- `python -m pytest tests/ -v`: **621 passed, 5 skipped, 0 failed**; collected = 626。
- Real external Reviewer: **UNVERIFIED_MISSING_CONFIG**（非阻塞）。
