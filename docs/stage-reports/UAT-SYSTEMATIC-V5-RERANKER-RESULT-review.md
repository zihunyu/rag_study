# UAT systematic v5 Reranker 结果独立审核

审核结论：`APPROVED:UAT_SYSTEMATIC_V5_LLM_V4_EXECUTION`

- 唯一获批的 v5 Reranker 执行已完成：requests=39、completed=39、failed=0、UNKNOWN=0、
  top-2 Gate=39/39、automatic retries=0。checkpoint SHA-256 为
  `188aded52174b60e399ea9d6448ffe383d5c553a050418581bfd20388e256317`。
- 独立结构化复核确认 checkpoint 有 39 条记录且全为 `COMPLETED`；manifest
  `request_count=39`，每条 retry=0、positive rank 均在 top-2，禁止字段命中=0。
- combined-v5 Gate SHA-256 为
  `ad5920fd2050797d4aaf3c952b68bffc515e9cc9adadb3a3dfd7f6eaf1f48d6b`；其 revision 为
  `uat-combined-reranker-gate:v5`，包含 78 条结果且 gate-passed=78。source hashes 精确绑定
  v1=1、v2=1、v3=1、v4=36 与刚完成的 v5=39 checkpoint。
- LLM v4 checkpoint 不存在，`uat-results/v4` 条目为 0；在 Gate 已解锁时，未带
  `--approved` 的 LLM 命令仍被拒绝。执行后 secret scan finding=0，Docker=0、Zilliz write=0，
  没有新增提交。

依照已满足的用户条件授权，现仅放行 LLM v4：最多 78 次、每条最多一次、automatic retry=0，
且只可使用已冻结的 78/78 combined-v5 Gate。任何失败或 UNKNOWN 必须立即停止并提交结果复核。
全部结果必须保持 `PENDING_USER_RESULT_REVIEW`；不得自动标记 UAT 通过。
