# Changelog

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
