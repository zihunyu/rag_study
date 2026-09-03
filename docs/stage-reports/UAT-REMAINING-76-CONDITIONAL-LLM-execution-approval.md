# UAT 剩余 Reranker 与条件 LLM 执行授权

用户结论：`APPROVED:UAT_REMAINING_76_RERANKER_AND_CONDITIONAL_78_LLM_RETRY_ZERO`

- Candidate 1 使用 v1 已通过证据；
- Candidate 2 使用 proposal 1 / v2 已通过证据；
- 仅对剩余 76 条执行 Reranker，每条最多 1 次，自动重试 0；
- 任一剩余 Reranker 失败立即停止，LLM requests 必须为 0；
- 只有组合 Reranker 78/78 全部通过 Gate 后，允许最多 78 次 LLM；
- LLM 每条最多 1 次，自动重试 0；
- LLM 结果仍需用户审核，不能自动标记真实 UAT 通过；
- 新执行必须使用独立 checkpoint，不修改 v1/v2 证据。

