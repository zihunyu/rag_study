# ADR 0001：通过 HybridIndexPort 隔离向量后端

状态：Accepted

应用层只依赖 `HybridIndexPort`。本地使用持久 SQLite BM25/余弦实现；托管环境可使用 Zilliz，
私有部署可使用 Milvus。供应商 URI、数据库和 Collection 不得进入搜索业务逻辑。所有实现必须
通过相同的权限、时间、代际和检索契约测试。
