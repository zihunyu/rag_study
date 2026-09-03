# UAT 剩余 76 条 Reranker 与条件 LLM 执行计划

状态：`EXECUTED_PARTIAL_GATE_FAILED_LLM_NOT_STARTED`

## 用户授权与冻结范围

- 用户批准剩余 76 条 Reranker：每条最多 1 次、automatic retries 0，任一失败立即停止；
- 仅在合并 Gate 达到 78/78 后，批准 78 条 LLM：每条最多 1 次、automatic retries 0；
- 最终候选集合为 v1 已通过 candidate 1、v2 修订且已通过 candidate 2、原 candidates
  3–78；candidate 2 使用 proposal 1，其他问题保持用户已批准版本；
- 78 个 bundle ref/hash 已冻结；v1/v2 checkpoint hash 只读绑定；计划 SHA-256
  `5604d295b174f164f1d0d2d4f78b8bec4f5f69625d7c5bfbdf736a165c852b81`。

## Reranker continuation v3

- checkpoint：`uat-reranker-v3.json`，当前不存在；
- 只接收 candidates 3–78 共 76 个 bundle；max requests 76、top-k 2、retry 0；
- 每次响应验证完整 4 项排列，先落盘 ranked IDs、positive rank、response count 与 Gate；
- 任一 FAILED/UNKNOWN/Gate failure 全局停止，不生成 combined Gate，LLM 固定 0；
- 已完成 resume 不重复请求，FAILED/UNKNOWN 阻断恢复。

## Combined Gate 与条件 LLM v2

- combined Gate 只接受 v1 结果 1 条、v2 结果 1 条、v3 结果 76 条；每条必须 COMPLETED、
  positive rank ≤2，且 question/bundle/evidence/rank 与冻结输入一致；
- aggregate 只含匿名 IDs、rank、hash、Gate 与 checkpoint provenance，不含正文；
- LLM checkpoint：`uat-llm-v2.json`，结果目录：`uat-results/v2`，当前均为空；
- LLM 仅使用各 bundle 的 top-2 evidence；输出必须满足 status/answer/citation schema，citation
  只能来自所选 evidence 且必须覆盖 expected positive；任一失败立即停止；
- 答案只落本地结果文件；checkpoint 无 question/content/answer；完成 78/78 后仍为
  `PENDING_USER_RESULT_REVIEW`，不得自动标记 UAT PASSED。

## 验证与边界

- fake 全通过验证 76 Reranker + combined 78 + 78 LLM；resume 均不重复；
- fake Reranker Gate failure 仅请求 1 次并阻断 combined Gate/LLM 0；输入变异在请求前拒绝；
- 完整质量门：294 tests passed、Ruff 302 files、mypy 107 source files、frontend、OpenAPI、
  config、全部本地 UAT artifacts 与 secret scan 通过；38 checks、failed 0、skipped 0；
- 本地准备阶段网络调用 0，v3/LLM-v2 checkpoint、combined Gate、results-v2 均不存在；
- NO_COMMITS、Docker 0、query Embedding 0、MinerU 0、Zilliz 0。

STAGE_REVIEW_REQUESTED:UAT_SYSTEMATIC_REVISION_V4_READY
