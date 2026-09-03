# 真实 MinerU DOCX→PDF v1 结果审批

审批结论：`APPROVED:REAL_MINERU_DOCX_PDF_V1_10_OF_10_LOCATOR_20_OF_20`

- 唯一一次真实执行：51 requests = create 10 + upload 10 + poll 21 + download 10；
- provider done 10/10，FAILED/UNKNOWN 0，自动重试与 failover 0；
- artifact 10、nodes/chunks/locators 302；
- expected/matched locator 20/20；
- 10 份派生 PDF 与原 DOCX 源哈希均保持；
- checkpoint 不含 URL、Token、原文件名、响应正文或文档正文；
- 扫描范围 10/10 locator 10/10，Embedding v2 669/669 保持成功；
- 新增 scan + DOCX chunks 合计 459，未加入既有 669 Embedding；
- 完整质量门 274 passed，Ruff、mypy、frontend、secret scan 通过；
- 无提交、无 Docker、无 Reranker/LLM/Zilliz 请求或写入。

