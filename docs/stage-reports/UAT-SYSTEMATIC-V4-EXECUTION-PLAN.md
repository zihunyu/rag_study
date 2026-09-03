# UAT systematic v4 Reranker 与条件 LLM 执行计划

状态：`EXECUTED_PARTIAL_GATE_FAILED_LLM_NOT_STARTED`

## 冻结输入与授权

- 用户批准 systematic revision v4 全部 75 条；
- Reranker v4：max 75、top-k 2、automatic retries 0，任一失败立即停止；
- 仅 combined-v4 78/78 Gate 通过后，条件批准 LLM v3：max 78、automatic retries 0；
- review、manifest、v1/v2/v3 checkpoint 与 78 个最终 bundle refs/hashes 均绑定执行快照；
- execution plan SHA-256：`7baad3acbac41cc377ed474a83200aecb99808b49526ae4d9c8d3ef60142721c`。

## Reranker v4

- 固定 checkpoint：`uat-reranker-v4.json`，只读 75 个 revised bundles；
- snapshot 绑定 revision questions、evidence IDs/content hashes/locators、manifest 与旧 PASS
  provenance；
- 完整 4 项响应排序后先保存 rank、positive rank、response count 与 Gate；
- FAILED/UNKNOWN/Gate failure 立即停止，禁止 combined-v4 与 LLM；
- COMPLETED resume 不重复，失败 resume 阻断。

## Combined Gate v4 与 LLM v3

- combined-v4 严格组合 v1 candidate1、v2 candidate2、v3 candidate3、v4 revised 75；
- 必须 78 unique、全部 COMPLETED/Gate true/positive rank ≤2，且输入 hash 一致；
- aggregate 只含匿名 IDs、hash、rank 与 provenance，不含正文；
- LLM checkpoint：`uat-llm-v3.json`；结果目录：`uat-results/v3`；
- LLM 绑定最终 78 bundle snapshot 与 combined hash，max 78、retry 0；严格 schema、citation
  subset 与 expected-positive Gate；任一失败停止；
- 结果为 `PENDING_USER_RESULT_REVIEW`，不得自动标记 UAT PASSED。

## 当前状态

- fake 全通过：75 Reranker + combined 78 + LLM 78；两阶段 resume 无重复；
- fake 首条 Reranker failure：request 1、combined 不生成、LLM 0；
- snapshot/hash mutation 在请求前拒绝；checkpoint 不含 question/content/answer；
- 当前 `executed=false`，v4/combined-v4/LLM-v3/results-v3 均不存在；
- NO_COMMITS、Docker 0、准备阶段网络 0。
- 完整质量门：299 tests passed、Ruff 314 files、mypy 108 source files、frontend、OpenAPI、
  config、全部 UAT artifacts 与 secret scan 通过；40 checks、failed 0、skipped 0。

STAGE_REVIEW_REQUESTED:UAT_SYSTEMATIC_REVISION_V5_READY
