# 五类真实格式统一验证审批

审批结论：`APPROVED:REAL_FORMAT_VALIDATION_NON_ASR_50_OF_50_LOCATOR_78_OF_78`

- 非 ASR 五类真实样本 50/50；
- chunks 1,128；expected/matched locator 78/78；
- 文本 PDF 10/10、PPTX 10/10、表格 10/10、扫描/图片 10/10、DOCX 10/10；
- `format_quality_ready=true`、`real_acceptance=true`；
- 50 份源样本未修改，10 个历史 checkpoint 哈希匹配；
- Embedding 已覆盖 669/1,128，未覆盖 459；新计划最多 46 batches、retry 0、
  Zilliz write 0，尚未批准或执行；
- UAT 候选 78 条全部 `PENDING_USER_REVIEW`，Reranker/LLM 请求 0；
- 独立定向审核 9 passed、Ruff、mypy 通过；开发完整质量门 275 passed；
- 无提交、无 Docker，本汇总阶段新增外部调用 0。

