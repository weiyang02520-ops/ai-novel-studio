# External AI Review Request

## 请重点检查

* Core/Adapter 分层是否干净(Core 是否仍隐含 UI 依赖)
* SecretStore 是否有 Key 泄露路径(文件/日志/异常消息)
* 配置校验逻辑与错误消息质量
* M0 测试覆盖是否有明显缺口
* checkpoint 脚本的 remote 自动解析是否健壮

## 不要做什么

* 不要无证据推翻设计(设计已过两轮审核)
* 不要只看文档 — 请运行 `python -m pytest tests/` 和 CLI 验证

## 优先检查的路径

```
core/config.py
llm/secret_store.py
adapters/cli/main.py
tests/test_m0.py
scripts/ai_checkpoint.py
```
