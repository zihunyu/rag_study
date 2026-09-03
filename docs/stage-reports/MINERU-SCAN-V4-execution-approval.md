# MinerU scan-v4 真实执行授权

授权结论：`APPROVED:MINERU_SCAN_V4_REAL_EXECUTION_RETRY_ZERO`

- 范围：扫描/图片 10 份；
- checkpoint：`mineru-scan-attempt-v4.json`；
- 自动重试 0、Token failover 0；
- 10/10 文件与 10/10 locator 成功后自动进入既有 DOCX v1 授权；
- 任一失败停止，不得第二次执行 scan-v4。

