# UAT Candidate 2 Reranker v2 诊断结果

状态：`COMPLETED_GATE_PASSED_AWAITING_RESULT_REVIEW`

- 严格按用户授权仅执行 1 次，automatic retries 0；
- request 1、completed 1、Gate passed true；
- positive rank 1，response index count 4，ranked evidence ID count 4；
- checkpoint：`uat-reranker-v2.json`，SHA-256
  `7fccd3f4aa9eff6fbe0128753bdf9e51b01cb0da932248838044542b9e031eb1`；
- checkpoint 禁止字段 0、秘密值 0、Endpoint 值 0，不含 question/content 或响应正文；
- proposal、failure review、Reranker v1、revision 与 diagnostic bundle 的冻结 hash 均不变；
- LLM requests 0；Embedding、MinerU、Zilliz 新增调用 0；
- 未执行第二次 v2；该单条诊断通过不自动恢复其余 Reranker，也不解锁 LLM；
- `real_uat_passed=false`，等待结果审核与后续范围决策。
- 执行后完整质量门：292 tests passed、Ruff 296 files、mypy 107 source files、frontend、
  OpenAPI、config、本地 UAT artifact 复验与 secret scan 全部通过；37 checks、failed 0、
  skipped 0。

STAGE_REVIEW_REQUESTED:UAT_CANDIDATE_2_RERANKER_V2_RESULT
