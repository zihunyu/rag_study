# 真实 Embedding 执行审核 1

审核结论：`BLOCKED:EMBEDDING_HTTP_400_NO_RETRY`

## 独立核对

- 只恢复了 Embedding；MinerU checkpoint、请求数与未知状态完全未变；
- Embedding 仅第 1 批发出 1 个真实请求，收到 HTTP 400 后立即停止；
- attempted 1/21、completed 0、vectors 0、automatic retries 0、Zilliz writes 0；
- checkpoint 保留 669-chunk snapshot、21-batch manifest、首批 chunk ID 映射和
  `UNKNOWN_OUTCOME`，不含正文、API Key 或 Base URL；
- DOCX、Reranker、LLM 请求均为 0；
- 修复后独立质量门：248 passed、Ruff lint、237 files format、mypy 99 source files。

## 本地长度诊断

- 总计 669 chunks / 43,048 characters；最大单 chunk 624 characters；
- 原顺序第 1 批 32 chunks / 12,620 characters，是 21 批中最大；
- 其余批次多数显著更小，最小 585 characters；
- 因响应正文未持久化，无法确认 HTTP 400 的供应商错误类型；首批总输入不均衡是合理
  怀疑，但不是已证明根因。

## 下一步边界

- 禁止自动重跑；
- 如用户批准新的 Embedding 尝试，先实现并审核稳定、确定性的长度均衡分批方案，
  仍限制 669 chunks、最多 21 batches、自动重试 0、Zilliz 写入 0，并安全记录数字状态与
  非敏感供应商 error code/type；
- MinerU 仍需先核对 Precision API Token/Endpoint/权限与供应商侧任务，再取得新的重试授权。

