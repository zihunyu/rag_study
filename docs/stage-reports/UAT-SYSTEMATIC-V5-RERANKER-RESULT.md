# UAT systematic v5 Reranker 执行结果

审查状态：`STAGE_REVIEW_REQUESTED:UAT_SYSTEMATIC_V5_RERANKER_RESULT`

- 唯一获批命令已执行一次：`scripts/run_uat_systematic_v5.py reranker --approved`。
- Reranker v5：requests 39、completed 39、failed 0、UNKNOWN 0、positive top-2 Gate 39/39、automatic retries 0；每条只发送一次，未重跑。
- v5 checkpoint：`provider-checkpoints/uat-reranker-v5.json`，SHA-256 `188aded52174b60e399ea9d6448ffe383d5c553a050418581bfd20388e256317`。
- 严格组合 Gate 已生成：`final-validation/uat-combined-reranker-gate-v5.json`，SHA-256 `ad5920fd2050797d4aaf3c952b68bffc515e9cc9adadb3a3dfd7f6eaf1f48d6b`；来源严格为 v1=1、v2=1、v3=1、v4=36、v5=39，candidate/gate-passed=78/78，top-2，`llm_execution_unlocked=true`。
- 按审批边界，LLM v4 未执行：checkpoint 不存在、结果目录条目 0、LLM requests 0。Embedding/MinerU 请求 0，Zilliz write 0，Docker 0，commit 0。
- checkpoint/Gate 中 `question`、`content`、`answer`、API key、base URL、endpoint 字段命中均为 0；执行后 secret scan finding 0。
- 结果不自动标记 UAT 通过。虽然 78/78 Gate 已解锁 LLM，LLM v4 仍须先经审批窗口复核；若放行，最多 78 次、每条一次、retry 0，结果仍待用户审核。
