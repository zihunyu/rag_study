# UAT candidate 2 Reranker v2 诊断结果审批

审批结论：`APPROVED:UAT_CANDIDATE_2_PROPOSAL_1_RERANKER_TOP1`

- 唯一请求 1、completed 1、Gate passed；
- positive rank 1、完整排序 4 项；
- automatic retries 0、第二次执行 false；
- checkpoint 不含 question/content/URL/key/响应正文；
- v1 失败证据与全部输入哈希未修改；
- LLM requests 0；
- 单条诊断通过不等于 UAT 全部通过。

下一阶段需组合 candidate 1 的 v1 通过证据、candidate 2 的 v2 通过证据，并仅处理剩余
76 条；全部 Reranker Gate 通过后才可执行 LLM。

