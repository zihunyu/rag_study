# MinerU scan-v4 本地执行器审批

审批结论：`APPROVED:MINERU_SCAN_V4_RUNNER_READY_AWAITING_REAL_RETRY_AUTHORIZATION`

- 合法中间状态覆盖 `waiting-file/uploading/pending/running/processing/converting`；
- 成功状态为 `done/completed/success`；明确失败状态为
  `failed/error/canceled/cancelled`；未知状态单独 fail-closed；
- fake-clock 路径验证中间状态依次轮询并 sleep 后成功；
- 固定 `execute-scan-v4` 与 `mineru-scan-attempt-v4.json`，当前 checkpoint 不存在；
- 执行前复核原始、scan-v2、scan-v3 三个旧 checkpoint 哈希；
- DOCX v1 授权保留，scan-v4 未 10/10 成功前 fail-fast；
- 独立定向审核 50 passed、Ruff、mypy 通过；开发完整质量门 264 passed；
- 本地修正新增网络调用 0。

scan-v3 已按用户授权执行并结束，不能把该授权扩展为 scan-v4。

