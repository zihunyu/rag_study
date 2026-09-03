# G1 审核批准

结论：`APPROVED:G1_NO_COMMITS`

## 独立证据

- Python 3.12.13；
- G1 配置 `gate_ready=true`，0 blocker；
- pytest：81 passed；
- Ruff lint/format、mypy strict、npm、OpenAPI、SQLite schema v2、原生入口、无 Docker和密钥扫描：全部通过；
- Worker 单任务异常隔离、QUEUED/RETRY_WAIT/RUNNING 取消与 Version 同步、取消后重试和原件恢复 SHA-256 校验：代码与回归测试通过；
- 所有结果保持未提交，`config/.env` 未跟踪且未输出值。

## G2 批准边界

- 只开放 WBS-30 索引与独立检索；
- 允许实现 MySQL/Redis 适配、Zilliz Cloud Dense + 原生 BM25、中文 Analyzer、ACL 过滤、security watermark、索引代际、应用层 RRF、Reranker 和 `/search`；
- ASR 与所有真实格式样本统一留 G4；LLM 与 `/ask` 留 G3；
- 对 Zilliz Cloud 先做只读兼容检查；如目标 Collection 不存在，需要创建或修改云资源时先报告，不自动执行；
- 保持 `NO_COMMITS`，到定时停止点保存未提交工作区并停止。

