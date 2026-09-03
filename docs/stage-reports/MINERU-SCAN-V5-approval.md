# MinerU scan-v5 本地执行器审批

审批结论：`APPROVED:MINERU_SCAN_V5_RUNNER_READY_AWAITING_REAL_RETRY_AUTHORIZATION`

- scan-v4 已完成 4 份并在第 5 份 TIFF 返回 `-60002` 后停止；完成结果不重传；
- 单帧 TIFF 使用确定性无损 PNG 派生，原始哈希不变，匿名固定路径与原子 manifest；
- `execute-scan-v5` 只处理 positions 5–10 共 6 份，checkpoint 当前不存在；
- max files 6、max requests 198、expected locators 6、retry 0、failover 0；
- v5 成功 6/6 后与 v4 完成 4/4 合并复核总 locator 10/10；
- DOCX v1 既有授权保留，等待合并扫描结果 10/10；
- 独立定向审核 53 passed、Ruff、mypy 通过；开发完整质量门 267 passed；
- 本地准备新增网络调用 0。

scan-v4 授权已经执行并结束，不能扩展为 scan-v5。

