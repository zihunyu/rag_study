# UAT systematic v5 LLM v4 执行结果

审查状态：`STAGE_REVIEW_REQUESTED:UAT_SYSTEMATIC_V5_LLM_V4_RESULT`

- 唯一获批命令已执行一次：`scripts/run_uat_systematic_v5.py llm --approved`。
- LLM v4：requests 78、completed 78、failed 0、UNKNOWN 0、automatic retries 0；每条只发送一次，未重跑。
- citation Gate 78/78，expected-evidence coverage Gate 78/78；所有 78 条结果均为 `PENDING_USER_RESULT_REVIEW`，`real_uat_passed=false`。
- LLM checkpoint：`provider-checkpoints/uat-llm-v4.json`，SHA-256 `7163f71791a974afac036e6eb8a6106f0de870275da47564b4d44359753cbb62`。
- 结果路径：`uat-results/v4`，78 个结果文件；content-free result snapshot SHA-256 `e3cee2518b5b783eba81f738704b0da01ba7c66cfb1ae627386ea21c11651045`。
- 双向恢复一致性：completed checkpoint → 结果 SHA 匹配 78/78；结果文件 → completed checkpoint/ref/SHA 匹配 78/78。
- checkpoint 中 `question`、`content`、`answer`、API key、base URL、endpoint 字段命中 0；执行后 secret scan finding 0。
- 本阶段 LLM provider requests 78；Reranker/Embedding/MinerU 新请求 0，Zilliz write 0，Docker 0，commit 0。

结果现待用户审核；不得因本轮 78/78 Gate 自动标记 UAT PASSED 或继续执行其他阶段。
