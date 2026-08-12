# 项目状态

> 最后更新: 2026-08-12 12:47 (自动生成, 来源: PROJECT_STATE.md)

## 当前阶段

**M0 Final External Review fixes completed. Awaiting External ChatGPT re-review.**
(M0 FINAL Gate 未授权; M1 NOT AUTHORIZED。)

## 本轮变更

**update 1 files**(新增 1 / 修改 1 / 删除 0)

## 已验证内容

- 单元测试 **57/57 PASS**(46 test_m0 + 11 test_checkpoint)。
- checkpoint 文件集合: 新目录内嵌套文件/空格文件名/Unicode 文件名全部进入扫描集合(真实验证)。
- CLI 全错误路径无 traceback: 非法 JSON/root list/models string/capabilities 非 object/temperature bool/max_context float/未知字段/敏感字段/非法 int/SecretStore unavailable — 全部人类可读 + exit 码合理。
- SecretStore: NOVEL_DISABLE_KEYRING=1 时 get/set/delete/exists 一致返回 BACKEND_UNAVAILABLE(测试覆盖)。
- Core/Adapter 边界静态检查: core/ llm/ tools/ agents/ 无 argparse/click/终端 IO/CLI 依赖/GUI 框架; SecretStore 无终端交互。
- 依赖: 正式仅 keyring, dev 仅 pytest(未新增)。

## 未验证内容

- Windows Credential Manager 真实读写验证(本轮将执行, 见下文 REAL_ENV)。
- 联网测试(M2 config test-provider 才做)。
- M1+ 功能(项目/章节 CRUD)未实现。

## 已知问题

- **GitHub 仓库当前可见性为 public**(API 实测, 创建时曾为 private) — 若需私有请用户在 GitHub 网页端修改, 我未擅自改动。这是事实记录, 不是本轮开发任务。
- Linux headless 无凭据服务时, SecretStore 写入会明确报错(BACKEND_UNAVAILABLE)并建议环境变量(设计如此, 不降级明文)。

## 下一步

- P0: 等待 External ChatGPT re-review(M0 FINAL Gate)。
- P1: 仅在明确授权后进入 M1(NOT AUTHORIZED YET)。
