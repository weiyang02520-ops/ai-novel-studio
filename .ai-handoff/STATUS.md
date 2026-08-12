# 项目状态

> 最后更新: 2026-08-12 17:07 (自动生成, 来源: PROJECT_STATE.md)

## 当前阶段

**M2 FINAL CLOSEOUT complete. Awaiting External ChatGPT final review.**
(M2 未宣布 PASS; M3 NOT AUTHORIZED。)

## 本轮变更

**update 1 files**(新增 1 / 修改 1 / 删除 0)

## 已验证内容

- 单元测试 **385/385 PASS**(M2 CLOSEOUT 新增 71: URL 校验允许 5/拒绝 16/set 写前/运行时/secret 不泄漏、tool_call delta consumer 断言 4、close 生命周期 4、CLI 更新)。
- Acceptance(A-I, 真实 CLI + local mock): A test-provider 成功 PASS / B chat 流式拼接 PASS / C keyless 无 Authorization PASS / D 401 安全错误 PASS / E 503→200 retry 请求数 2 PASS / F 429 请求数 1 PASS / G stream interrupt 部分保留不重试 PASS / H usage summary PASS / I validate 离线(httpx.Client monkeypatch 断言)PASS。
- Secret 安全: 全局泄漏断言(401/403/500/network/malformed/chat CLI/test-provider/URL 拒绝路径 → stdout/stderr/exception/usage/settings 均无 fake secret)PASS。
- M0 regression: test_m0 + test_checkpoint **66/66 PASS**; M1 regression: novel/chapter/history/transaction 全部 PASS。
- Windows Credential Manager: REAL_ENV_CONFIRMED(前轮)。

## 未验证内容

- 真实外部 Provider 成功调用(生产配置缺 secret_reference; 用户配置 Key 后可 test-provider 验证)。
- AI Agent(Runtime/主编/Writer/Reviewer, M3+)未实现。

## 已知问题

- **GitHub 仓库当前可见性为 public**(API 实测, 创建时曾为 private) — 若需私有请用户在 GitHub 网页端修改, 我未擅自改动。这是事实记录, 不是开发任务。
- Linux headless 无凭据服务时, SecretStore 写入会明确报错(BACKEND_UNAVAILABLE)并建议环境变量(设计如此, 不降级明文)。

## 下一步

- P0: 等待 External ChatGPT M2 review。
- P1: M3(Agent Runtime + Chief Editor)仅在明确授权后开始。
