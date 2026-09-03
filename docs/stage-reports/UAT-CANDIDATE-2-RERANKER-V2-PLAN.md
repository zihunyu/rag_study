# UAT Candidate 2 Reranker v2 单条诊断计划

状态：`EXECUTED_COMPLETED_GATE_PASSED`

## 冻结输入

- 用户选择 proposal 1，并批准仅 1 次 Reranker v2 诊断、automatic retries 0；
- proposal、failure review、Reranker v1 checkpoint 与原 bundle 的 SHA-256 均在生成前验证；
- 独立 revision ref：`uat-result-review/candidate2-revision-v2.json`，SHA-256
  `98eedff851e5ef978616cc6fdb1b2605aab97beb25813bad56884286ad9acfb1`；
- 独立 bundle ref：`uat-diagnostic-bundles/v2/180354c6037553b54e3e.json`，SHA-256
  `3b349bb9312483f5e01dafe3ef815ae3579c33e0cca41f6f44cdf9a5229f5d89`；
- 4 documents 的 evidence ID、content、content hash、locator 与 role 和原失败 bundle
  逐项一致；question 精确等于 proposal 1，candidate 使用派生 revision ID；
- manifest 绑定 original candidate、proposal number/hash、failure-review hash、v1 checkpoint
  hash 与 original bundle hash；83 个既有输入生成前后 hash 不变。

## 固定执行边界

- 脚本：`scripts/run_uat_reranker_diagnostic_v2.py`，仅 `plan/execute`；
- 单 bundle、max requests 1、positive top-k 2、automatic retries 0；
- checkpoint 固定 `uat-reranker-v2.json`；v1 只读；
- LLM request 0 且 runner 不包含 LLM 路径；Embedding/MinerU/Zilliz 0；
- 完整响应排序验证后，先保存 ranked evidence IDs、positive rank、response count 与 Gate，
  再将 Gate failure 转为 FAILED；checkpoint 不保存 question/content；
- 任一完成或失败后均不得第二次请求；resume 只允许读取已完成结果，不重复调用。

## 验证

- fake pass、fake fail、Gate failure 排序/排名留存、预算 1、retry 0、resume 不重复、
  documents 不变且只改 question、LLM 0 均已测试；
- 完整质量门：292 tests passed、Ruff 293 files、mypy 107 source files、frontend、OpenAPI、
  config、本地 UAT artifact 复验与 secret scan 全部通过；37 checks、failed 0、skipped 0；
- 独立审核通过后仅执行一次：request 1、completed 1、Gate passed、positive rank 1、
  response indexes 4、retry 0；
- checkpoint SHA-256 `7fccd3f4aa9eff6fbe0128753bdf9e51b01cb0da932248838044542b9e031eb1`；
  禁止字段、秘密值与 Endpoint 值命中均为 0；
- LLM request 0，未运行第二次诊断；冻结输入 hash 全部不变。
- 执行后完整质量门：292 tests、Ruff 296 files、mypy 107 source files、frontend、OpenAPI、
  config、全部本地 UAT artifact 复验与 secret scan 通过；failed 0、skipped 0。

STAGE_REVIEW_REQUESTED:UAT_CANDIDATE_2_RERANKER_V2_RESULT
