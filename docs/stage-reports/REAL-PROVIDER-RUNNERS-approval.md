# 真实供应商执行器审批（修订 2）

审批结论：`APPROVED:REAL_PROVIDER_RUNNERS_READY_REV2_NO_COMMITS`

审批时真实 MinerU、Embedding、Reranker、LLM 调用均为 0，Zilliz 写入为 0。

## 独立复核证据

- 完整测试：248 passed，0 failed；
- Ruff lint：通过；Ruff format：235 files；
- mypy strict：99 source files，通过；
- dependency boundaries：通过，application 只依赖 contracts 端口；
- MinerU 使用官方 Precision v4 batch 签名上传协议，签名 URL 不落 checkpoint，
  不确定结果进入 `UNKNOWN_OUTCOME` 并禁止自动重发；
- 供应商 ZIP、规范化节点和 manifest 在现有本地 artifact 目录匿名原子落盘；
  checkpoint 只保留匿名引用、哈希、大小和计数；
- 规范化节点保留后续分块需要的文本/结构、1-based page 与 bbox，可对 10 份样本的
  expected locator 做严格复核；
- Embedding 固定 669 chunks、batch size 32、最多 21 batches，校验响应 index、数量、
  维度、有限值及 chunk 映射，checkpoint 不保存正文；
- UAT 已本地生成 78 条候选，全部 `PENDING_USER_REVIEW`，模型/网络调用为 0。

## 自动开放的真实执行

- 扫描 PDF / 图片：允许 MinerU 实际处理 10 份；自动重试 0；每文件最多 30 次状态轮询、
  轮询间隔 10 秒、低层 HTTP 总请求硬上限 330；同一文件失败不得切换 Token 重发；
- 当前 669 chunks：允许最多 21 个 Embedding 批次；自动重试 0；只保存本地结果，
  禁止写入 Zilliz；
- 两条流可并行；任一出现未知结果、预算异常、429、5xx、超时或 schema 异常，
  对应执行流立即停止并交回审核。

## 仍未开放

- DOCX 10 份的真实 MinerU 提交；
- UAT 候选审核前的 Reranker/LLM；
- Zilliz 真实样本向量写入；
- Docker 与任何 Git commit/merge/rebase/tag/push。

