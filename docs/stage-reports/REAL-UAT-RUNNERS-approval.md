# 真实 UAT Reranker / LLM 执行器审批

审批结论：`APPROVED:REAL_UAT_RERANKER_78_THEN_LLM_78_RETRY_ZERO`

- UAT bundles 78，每包 1 正例 + 3 同类干扰项；
- 这是 controlled locator-grounded UAT，不宣称完整生产检索 E2E；
- Reranker 最多 78 requests，positive 必须进入 top-2；任一失败全局停止；
- 只有 Reranker 78/78 全部完成并通过 Gate，才允许 LLM 最多 78 requests；
- LLM 输出必须为 status/answer/citation_ids，引用只能来自 bundle 且覆盖预期正例；
- 两阶段 automatic retries 0；query Embedding 0、Zilliz 0；
- checkpoint 完整绑定 question/positive/evidence/locator/ranking，恢复不会重复计费；
- 用户结果复核前 `real_uat_passed=false`；
- 独立定向审核 5 passed、Ruff、mypy 通过；开发完整质量门 286 passed；
- 两个 checkpoint 当前不存在，无提交、无 Docker，本地准备新增网络调用 0。

用户“候选先审核，再调用 Reranker/LLM”的条件已满足，允许按固定预算执行。

