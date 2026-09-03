# Embedding v3 与 UAT 全部批准清单审批

审批结论：`APPROVED:EMBEDDING_V3_RUNNER_AND_UAT_APPROVAL_ARTIFACT`

- UAT pending 原文件哈希匹配且字节不变；
- approved artifact 78/78 `APPROVED_BY_USER`，候选 ID/问题/locator/evidence 不变；
- Reranker/LLM/网络调用仍为 0；
- Embedding v3 输入动态重读 459 chunks：scan-v4 75、scan-v5 82、DOCX-PDF 302；
- chunk IDs 459 唯一、20 个 artifacts 哈希通过；
- batch size 10、max batches 46、retry 0、Zilliz write 0；
- checkpoint 当前不存在，不复用 Embedding v2；
- 独立定向审核 43 passed、Ruff、mypy 通过；开发完整质量门 279 passed；
- 无提交、无 Docker，本地准备新增网络调用 0。

用户真实 Embedding v3 授权已存在，审核通过后可立即执行。

