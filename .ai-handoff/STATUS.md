# 项目状态

> 最后更新: 2026-08-12 18:01 (自动生成, 来源: PROJECT_STATE.md)

## 当前阶段

**M3 implementation complete. Awaiting External ChatGPT M3 review.**
(M3 未宣布 PASS; M4 NOT AUTHORIZED。)

## 本轮变更

**add 13 new files**(新增 13 / 修改 1 / 删除 0)

## 已验证内容

- 单元测试 **450/450 PASS**(M3 新增 65: tools 35+1skip(symlink 权限) / runtime 19 / m3_cli 11)。
- Acceptance(A-K, 真实 CLI + localhost mock): A project_info PASS / B list_chapters PASS / C read_outline PASS / D read_character PASS / E search_memory PASS / F multi-round PASS / G weak fallback PASS / H runaway limit PASS / I read-only hash invariant PASS(22 文件字节一致)/ J localhost real HTTP tool-loop PASS(两请求: 带 tools schema → role=tool 回填 → grounded 回答"第 3 章")/ K real external UNVERIFIED_MISSING_CONFIG。
- Secret 安全: Chief 路径 401/tool error/round limit/local mock → stdout/stderr/usage 均无 fake key PASS。
- M0 regression: 66/66 PASS; M1 regression PASS; M2 regression(raw chat/stream/URLs/close/usage/test-provider)PASS。
- Windows Credential Manager: REAL_ENV_CONFIRMED(前轮)。

## 未验证内容

- 真实外部 Provider 成功调用 + 真实 Chief tool_call(生产配置缺 secret_reference; 用户配置 Key 后可验证)。
- AI Agent 写入(M4: update_outline/update_character/update_world/save_memory_entry)未实现。
- Writer/Reviewer Agent、写章节草稿(M5/M6)未实现。

## 已知问题

- **GitHub 仓库当前可见性为 public**(API 实测, 创建时曾为 private) — 若需私有请用户在 GitHub 网页端修改, 我未擅自改动。这是事实记录, 不是开发任务。
- Linux headless 无凭据服务时, SecretStore 写入会明确报错(BACKEND_UNAVAILABLE)并建议环境变量(设计如此, 不降级明文)。

## 下一步

- P0: 等待 External ChatGPT M3 review。
- P1: M4(Chief 写入工具: outline/character/world/memory)仅在明确授权后开始。
