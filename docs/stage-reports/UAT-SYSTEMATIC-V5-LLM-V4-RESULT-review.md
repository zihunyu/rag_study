# UAT systematic v5 LLM v4 结果独立审核

审核结论：`PENDING_USER_REVIEW:UAT_SYSTEMATIC_V5_LLM_V4_RESULTS`

- LLM v4 的唯一授权执行结果为 requests=78、completed=78、failed=0、UNKNOWN=0、
  automatic retries=0；citation Gate=78/78，expected-evidence coverage Gate=78/78。
- LLM checkpoint SHA-256 为
  `7163f71791a974afac036e6eb8a6106f0de870275da47564b4d44359753cbb62`。独立复核其
  78 条记录均为 `COMPLETED`、request_count=78、每条 retry=0，且不含正文、答案、凭据或
  endpoint 字段。
- `uat-results/v4` 的 78 个结果文件全部为 `PENDING_USER_RESULT_REVIEW`；每个 completed
  checkpoint 的 result ref/SHA 均匹配文件，且每个文件均可回指 completed checkpoint，双向
  一致性为 78/78。
- content-free 结果快照按有序的
  `{candidate_id, result_ref, result_sha256}` 列表规范化后重建为
  `e3cee2518b5b783eba81f738704b0da01ba7c66cfb1ae627386ea21c11651045`，与执行报告一致。
- 执行后 secret scan finding=0；本阶段仅 LLM provider requests=78，Reranker/Embedding/MinerU
  新请求=0、Zilliz write=0、Docker=0、无提交。

所有 78 条结果现已可供用户审核，但 `real_uat_passed=false`，不得因技术 Gate 通过而自动
声明 UAT 通过、继续后续阶段或改变既有 deferred 项。
