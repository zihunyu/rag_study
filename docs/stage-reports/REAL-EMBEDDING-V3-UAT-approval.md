# 真实 Embedding v3 与 UAT 候选审批

审批结论：`APPROVED:REAL_EMBEDDING_1128_OF_1128_AND_UAT_78_APPROVED`

- Embedding v3：46 requests / 46 completed batches / 459 vectors；
- v3 全部 1024 维 finite，459 chunk-ID/index 映射一致；
- v2 + v3 总覆盖 1,128/1,128 chunks，113 batches，未覆盖 0；
- retry 0、Zilliz write 0、第二次 v3 execution false；
- UAT pending 原文件哈希不变，approved 78/78 `APPROVED_BY_USER`；
- candidate ID/question/locator/evidence 不变，approval manifest 不含正文；
- Reranker/LLM/MinerU 新请求 0；
- 完整质量门 279 passed，Ruff、mypy、frontend、secret scan 通过；
- 无提交、无 Docker。

