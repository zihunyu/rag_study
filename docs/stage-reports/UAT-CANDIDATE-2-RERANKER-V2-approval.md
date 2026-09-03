# UAT candidate 2 Reranker v2 单条诊断审批

审批结论：`APPROVED:UAT_CANDIDATE_2_RERANKER_V2_SINGLE_REQUEST_RETRY_ZERO`

- proposal 1 精确替换原问题；
- 4 个 evidence 的 ID/content/hash/locator/role 逐项不变；
- 派生 candidate revision ID 与 v1 隔离；
- 固定 checkpoint `uat-reranker-v2.json`，当前不存在；
- max requests 1、top-2、retry 0；
- pass/fail 均先保存匿名完整 order/positive rank/Gate；
- LLM requests 0；
- 独立定向审核 9 passed、Ruff、mypy 通过；开发完整质量门 292 passed；
- 本地准备网络调用 0、无提交、无 Docker。

用户单条真实诊断授权已存在，允许执行一次。

