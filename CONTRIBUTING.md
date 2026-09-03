# 贡献指南

所有改动从 Issue 或可追踪任务开始，并在 PR 中说明风险、验证结果和是否涉及真实 Provider。

## 开发流程

1. 从最新 `main` 创建短生命周期分支。
2. 使用 `requirements.lock` 和 `frontend/package-lock.json` 安装依赖。
3. 修改行为时同时增加失败用例和正常用例。
4. 运行 `python scripts/run_quality.py`；前端交互改动还要运行 Playwright。
5. PR 必须通过 CI，至少一名 Code Owner 审核后才能合并。

不得在普通 PR 中启用真实计费调用。真实 Provider、Zilliz 写入和生产数据测试只能通过受保护的
`real-rag-acceptance` Environment 执行。任何密钥、Prompt 原文、用户问题和检索证据都不得进入
日志、截图或测试产物。

架构或安全边界变化需要在 `docs/adr` 新增/更新 ADR。提交信息应说明为什么修改，而不只是文件
变化；破坏性迁移必须附回滚步骤。
