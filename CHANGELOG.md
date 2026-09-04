# Changelog

## 2026-09-04

- Make index Saga retries attempt-aware and require exact manifest reconciliation before READY.
- Reject answer prose not fully covered by cited claims and render responses only from verified claims.
- Normalize production MySQL aggregate persistence with row-level optimistic concurrency and pooling.
- Make publication fail closed through durable switching intents and a transactional outbox finalization.
- Preserve semantic chunk structure/tokenizer context and carry table headers into generation evidence.
- Add ANN manifest/LRU retirement, calibrated fusion, cooperative Worker cancellation, and truthful health.
- Harden all containers and supply-chain artifacts with digests, non-root execution, CycloneDX and hashes.

## Unreleased

- 增加真实本地 BM25/余弦检索、Token/结构化分片和持久索引链路。
- 增加生产 Runtime Profile、真实 LLM 生成、验收证据哈希和安全传输门禁。
- 增加批量 Zilliz 写入、并发 Hybrid Search、查询感知融合与近重复多样性。
- 增加 RAG 质量指标、Tracing、CI、覆盖率、锁文件、容器和前端 E2E。
- Production Runtime 改为 MySQL 权限投影/Release 水位、Redis 验证答案缓存、真实 MinerU
  Parser、OIDC Discovery/JWKS 和签名验收凭据。
- Zilliz Collection 升级 `current_version` Schema，批量 upsert、生命周期部分更新及清理已在
  真实云端完成验证。
- Parser 与 Zilliz provisioning 按职责拆分；浏览器 E2E 覆盖上传、独立 Worker、复核、发布、
  问答和引用。
- 未审核索引改为最高密级 fail-closed 投影；审核 API 冻结 visibility、classification、ACL 和
  有效期，OIDC principal 增加 clearance。
- 增加独立 Claim Verifier、JSON Evidence、URL/凭证输出策略、带 kid 的引用签名 keyring。
- Local 检索升级 FTS5 与持久 USearch 快照；融合使用分数校准，并在 Rerank 后去重。
- Production 增加 MySQL 上传/生命周期/RAG/引用/治理仓储、Redis 队列及 MySQL 索引 Saga。
- 真实验收收敛为 10 条业务 Gold、1/5/20 Chunk、三种恶意格式及 60 次/20万输入/2万输出
  Token 硬预算，且禁止自动重试。
- CI 拆分并固定 Actions SHA，增加安装 Smoke、依赖审计、CodeQL、Trivy、SBOM、Cosign 和
  provenance。
