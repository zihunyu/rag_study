# MinerU scan-v3 真实执行授权

授权结论：`APPROVED:MINERU_SCAN_V3_REAL_EXECUTION_RETRY_ZERO`

- 范围：`pdf_scanned_or_image` 10 份；
- checkpoint：`mineru-scan-attempt-v3.json`；
- 自动重试 0、Token failover 0；
- max requests 330、poll 30、interval 10 秒；
- 10/10 文件且 locator 10/10 成功后，才可继续既有 DOCX v1 授权；
- 任一失败停止，不得第二次运行 scan-v3。

