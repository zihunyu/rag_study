# UAT systematic revision v4 Reranker 与条件 LLM 授权

用户结论：`APPROVED:UAT_SYSTEMATIC_V4_75_RERANKER_AND_CONDITIONAL_78_LLM_RETRY_ZERO`

- 75 条系统性修订全部批准；
- Reranker v4 仅处理这 75 条，每条最多 1 次，自动重试 0；
- 既有通过证据：v1 Candidate 1、v2 Candidate 2、v3 Candidate 3；
- 任一 Reranker v4 失败立即停止，LLM requests 必须为 0；
- 只有组合 Reranker 78/78 全部通过后，允许最多 78 次 LLM；
- LLM 每条最多 1 次，自动重试 0；
- LLM 结果仍需用户复核，不能自动标记 UAT 通过；
- 新执行必须使用 v4/LLM-v3 独立 checkpoint，不修改 v1/v2/v3。

