# 供应商合同修正审批

审批结论：`APPROVED:PROVIDER_CONTRACT_CORRECTION_LOCAL_ONLY_NO_COMMITS`

本审批只批准本地协议、预算和证据修正；新增真实网络调用为 0。

## 独立证据

- 完整测试：259 passed；
- Ruff lint：通过；Ruff format：239 files；
- mypy strict：99 source files，通过；
- MinerU 保持官方 Precision v4 协议；未来安全保存 HTTP status、标量 provider code/type
  与 trace hash，不保存 msg/body/URL/Token；
- DashScope `text-embedding-v4` 约束为 1024 维、单请求最多 10 文本；配置 32 会在读取
  正文、写 checkpoint 和网络请求前 fail-fast；
- 669 chunks 新计划为 67 batches，使用独立 attempt/checkpoint，旧失败 checkpoint
  字节级不变；新 attempt `approved=false`、自动重试 0、Zilliz 写入 0；
- 当前真实计数保持 MinerU create 1 / 其他 0，Embedding failed request 1 / completed 0。

## 等待用户动作

- 将 `config/.env` 的 `EMBEDDING_BATCH_SIZE` 改为 `10`；
- 明确批准新的 67-batch Embedding attempt；
- 明确批准 MinerU 再次创建请求；
- 如需处理 DOCX，另行批准 DOCX 10 份真实提交。

