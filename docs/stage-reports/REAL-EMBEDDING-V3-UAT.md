# Embedding v3 真实结果与 UAT 批准状态

状态：`REVIEW_REQUESTED_REAL_EMBEDDING_V3_RESULT_AND_UAT_APPROVED`

## Embedding v3

- attempt：`embedding-real-attempt:v3-format-remainder`；
- checkpoint：`embedding-format-remainder-attempt-v3.json`，与 v2 完全隔离；
- 输入：scan-v4 75 + scan-v5 82 + DOCX-PDF 302 = 459 chunks；
- 输入只含成功 artifacts 的非空 `display_text`，chunk ID 使用稳定 node ID，459 个唯一；
- artifacts 20 个，读取时均通过 manifest/hash 完整性复核；
- requests 46、completed batches 46/46、vectors 459；
- 全部向量 1024 维 finite，chunk-ID/index 映射正确；
- automatic retries 0、Zilliz write 0、第二次 execution false；
- checkpoint 敏感字段 0，不含正文、URL、API key、message/body/input；
- Embedding v2 保持 669 chunks / 67 batches 且 SHA-256 不变；
- 总覆盖达到 1,128/1,128 chunks、113 batches，未覆盖 0。

## UAT approval

- pending snapshot SHA-256 匹配冻结值且原文件字节级不变；
- approved artifact 78/78 `APPROVED_BY_USER`；
- candidate ID、question、locator、evidence 全部保持；
- approval manifest 不含问题正文；
- `require_user_review_before_model_calls(approved)` 通过；
- 本轮 Reranker/LLM/MinerU requests 0。

## 安全与工程边界

- Zilliz writes 0、Docker 0、Git commits 0；
- secret scan 0 findings；
- 完整质量门：279 passed、Ruff lint/format 272 files、mypy 102 source files、frontend、
  Embedding v3 completed plan、UAT approval、real-format evidence 与 secret scan 全部通过。

STAGE_REVIEW_REQUESTED:REAL_EMBEDDING_V3_RESULT_AND_UAT_APPROVED
