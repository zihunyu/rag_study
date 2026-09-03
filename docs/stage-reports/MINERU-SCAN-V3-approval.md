# MinerU scan-v3 本地执行器审批

审批结论：`APPROVED:MINERU_SCAN_V3_RUNNER_READY_AWAITING_REAL_RETRY_AUTHORIZATION`

- 新 Token 只做本地格式校验：1 个、无 `Bearer ` 前缀或空白、官方 v4 地址；
- `execute-scan-v3` 固定使用 `mineru-scan-attempt-v3.json`，当前 checkpoint 不存在；
- 两个旧 MinerU checkpoint SHA-256 固定且执行前必须同时复核；
- 范围为扫描/图片 10 份、locator 10、OCR 开、max requests 330、poll 30、
  interval 10 秒、自动重试 0、Token failover 0；
- DOCX v1 既有授权保留，但 scan-v3 未 10/10 成功前 fail-fast；
- 定向独立审核 47 passed、Ruff 与 mypy 通过；开发完整质量门 261 passed；
- 本阶段新增真实网络调用 0。

更新 Token 不等于批准新的真实重试；执行仍等待用户明确授权。

