# MinerU scan-v5 真实执行授权

授权结论：`APPROVED:MINERU_SCAN_V5_REMAINING_SIX_RETRY_ZERO`

- 仅处理 scan-v4 未完成的 positions 5–10，共 6 份；
- 第 5 份使用已审核的匿名无损派生 PNG；
- 不重传 scan-v4 已完成的 4 份；
- 自动重试 0、Token failover 0；
- v5 完成 6/6 且合并 locator 10/10 后自动进入既有 DOCX v1 授权；
- 任一失败停止，不得第二次执行 scan-v5。

