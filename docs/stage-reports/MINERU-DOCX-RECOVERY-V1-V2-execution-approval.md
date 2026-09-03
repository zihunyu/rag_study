# MinerU DOCX recovery-v1 与 v2 真实执行授权

授权结论：`APPROVED:MINERU_DOCX_RECOVERY_V1_THEN_V2_RETRY_ZERO`

- recovery-v1 只查询并下载 DOCX 第 1 份既有 batch，create/PUT 为 0；
- recovery 成功且第 1 份 locator 2/2 后执行 DOCX v2；
- DOCX v2 仅处理 positions 2–10 共 9 份，expected locators 18；
- 两阶段自动重试 0、Token failover 0；
- 任一失败停止，不得第二次运行对应阶段。

