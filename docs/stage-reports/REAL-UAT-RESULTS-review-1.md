# 真实 UAT 模型结果审核 1

审核结论：`BLOCKED:UAT_RERANKER_CANDIDATE_2_POSITIVE_NOT_IN_TOP_2`

- Reranker 唯一执行：requests 2、completed 1、failed 1、UNKNOWN 0、retry 0；
- 第 2 条失败码 `UAT_RERANKER_POSITIVE_NOT_IN_TOP_K`；
- LLM requests 0、checkpoint 不存在、results 0；
- Reranker v1 checkpoint 不含问题/证据正文、URL、key 或响应正文；
- 人工复核 artifact：`uat-result-review/reranker-failure-1.json`，documents 4；
- 旧 runner 未保存失败排序，因此明确 `provider_order_unavailable=true`、
  `positive_rank_unknown=true`，没有推测；
- 未来 runner 已修复为先保存匿名 order/rank 再记录 Gate 失败；
- 未来 v2 checkpoint 不存在、未批准、未执行；
- 完整质量门 286 passed，Ruff、mypy、frontend、secret scan 通过；
- `real_uat_passed=false`，不得启动 LLM 或解锁最终 Gate。

建议用户修改第 2 条候选，使问题能区分预期正例与同类干扰项；修改后先做单条 v2 诊断，
通过 top-2 后再规划剩余 UAT。

