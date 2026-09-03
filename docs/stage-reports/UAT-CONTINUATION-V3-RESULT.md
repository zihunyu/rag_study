# UAT Reranker continuation v3 结果

状态：`PARTIAL_GATE_FAILED_LLM_NOT_STARTED`

- v3 仅执行一次，request 2、completed 1、failed 1、UNKNOWN 0；
- automatic retries 0，第二次执行 false；
- 失败错误码 `UAT_RERANKER_V3_POSITIVE_NOT_IN_TOP_K`；positive rank 4/4；
- 失败记录保存完整 4 个 ranked evidence IDs、positive rank、response index count 与 Gate；
- checkpoint SHA-256：`72a2fdf766891a8414bc6a77848828d41d3bf96274fcb82094b55f209ce4b30e`；
- checkpoint 不含 question/content、URL、API key、Endpoint 或 provider message/body；
- v1/v2 checkpoint 与冻结 bundles hash 均不变；
- combined Gate 未生成；LLM checkpoint 不存在、request 0、results-v2 0；
- Gate PASS 集合为 3 条，原 candidates 4–78 共 75 条待系统修订审核；
- `real_uat_passed=false`，不得重跑 v3。

STAGE_REVIEW_REQUESTED:UAT_SYSTEMATIC_REVISION_V4_READY
