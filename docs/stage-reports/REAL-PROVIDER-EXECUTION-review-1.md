# 真实供应商执行结果审核 1

审核结论：`PARTIAL_APPROVAL:EMBEDDING_ORIGINAL_BUDGET_UNCONSUMED_MINERU_BLOCKED`

## 独立核对

- MinerU checkpoint：1 个文件、`UNKNOWN_OUTCOME/CREATE_BATCH`、低层请求数 1；
  upload/poll/download/completed/artifact 均为 0，自动重试和 Token failover 为 0；
- Embedding checkpoint 不存在，真实请求/批次/向量均为 0，原 669 chunks / 21 batches
  授权预算未被使用；
- DOCX、Reranker、LLM 请求和 Zilliz 写入均为 0；
- checkpoint 不含 URL、原文件名或正文；Token 字段只存在匿名 `token_slot`，没有凭据值；
- 本地修复后独立质量门：248 passed、Ruff lint、237 files format、mypy 99 source files。

## 审核决定

- MinerU：已经发生一次真实 create 请求，而旧执行版本没有保留数字 HTTP 状态；严格遵守
  自动重试 0，不批准再次创建。需先核对 Precision API 权限/Token/Endpoint 与供应商侧任务，
  再取得新的明确重试授权；
- Embedding：失败发生在任何外部请求之前，属于本地入口缺陷，不构成模型请求重试；
  修复后允许继续使用原授权，固定 669 chunks、最多 21 batches、自动重试 0、Zilliz 写入 0。

