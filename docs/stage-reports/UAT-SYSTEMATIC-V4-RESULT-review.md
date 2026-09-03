# UAT systematic v4 Reranker 结果审核

审核结论：`BLOCKED:UAT_SYSTEMATIC_V4_POSITIVE_RANK_3_TOP2_GATE_FAILED`

- 唯一执行 requests 37、completed 36、failed 1、UNKNOWN 0、retry 0；
- 失败 candidate revision ID：`8b6b08e289b402b1f741`；
- positive rank 3/4、完整匿名排序 4 项、Gate false；
- 组合 Gate 未生成，LLM-v3 requests 0；
- v4 checkpoint 不含 question/content/URL/key/响应正文；
- 不得重跑 v4；
- 既有 3 条 PASS + v4 36 条 PASS，共 39 条通过；
- 失败项与剩余未执行项共 39 条，需要更强区分性修订；
- 推荐保持 top-2，不降低验收标准，使用两个 positive-only distinctive terms 重写。

