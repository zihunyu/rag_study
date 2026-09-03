# Zilliz Cloud G2 创建计划（未执行）

状态：`VALIDATED:G2`

## 只读现状

- `database_session_usable=true`；
- `collection_exists=false`；
- `mutating_call_performed=false`；
- 未调用 create/drop/alter/insert/upsert/delete。

只读结果位于忽略目录 `artifacts/g2/zilliz-readonly.json`。官方 `MilvusClient`
`list_databases()` 返回数据库名列表，`has_collection()` 和 `describe_collection()` 分别用于
存在性与 Schema 查询。

## 精确 Dry-run

执行下列命令只生成计划，不连接或修改云端：

```powershell
& '.\.venv\Scripts\python.exe' scripts/plan_zilliz_collection.py
```

计划 revision：`zilliz-collection-plan:g2-v1`。数据库名和 Collection 名分别在获批执行时
从 `ZILLIZ_CLOUD_DATABASE`、`ZILLIZ_CLOUD_COLLECTION` 读取；计划文件不复制实际配置值。

Schema 固定 29 个字段，包括：

- 主键和层级：zilliz/tenant/space/corpus/document/version/chunk/parent；
- 安全与时态：visibility、ACL ARRAY、classification、permission revision、valid from/to；
- 检索发布：lifecycle、generation、analyzer revision、checksum；
- Dense vector、原始 `retrieval_text` 和 BM25 sparse vector；
- category/tag/product/applicable-version/region ARRAY。

索引包括 HNSW Dense、SPARSE_INVERTED_INDEX + BM25，以及 9 个过滤字段 AUTOINDEX。
`retrieval_text` 启用配置指定的中文 Analyzer，BM25 function 从原始文本生成 sparse vector。
安全读取一致性固定 Strong。

生成计划会输出 Schema fingerprint；当前计划没有执行器入口，审批前不能创建任何资源。
即使创建获批，insert/upsert/delete 仍需要后续独立的数据写入批准。

当前 pymilvus 2.6.17 兼容 fingerprint：
`e73c4c8e6f981a549f1b4d9daf7977cc3d22221f8ec8afda410062ce27aa0f92`。该 SDK
使用 `VARCHAR(max_length=65535, enable_analyzer=true)` 承载可分析文本；BM25 function、
Sparse field 和索引语义不变。

探测误判已修复：default 会话不依赖 `list_databases()` 返回名称，且禁止
`create_database(default)`、`use_database(default)`。用户释放容量后，本项目配置 Collection
已创建：29 fields、1 个 BM25 function、11 indexes、Loaded。未 drop 或修改其他资源。

审核诊断与最终复核已完成。生产/合成 writer 固定 `batch_size=1`，逐条 Strong 确认和
confirmed-only cleanup；readiness 等待有界。Loaded 时绝不重复 load，未 Loaded 的 load
返回异常必须由 readiness 证明后才继续。

最终真实验证：4 条逐条写入并确认；BM25/Dense/RRF、ACL、时态、代际、Strong、watermark
全部通过；4 条全部删除，Strong remaining=0；未 drop 或修改其他资源。

参考：[Zilliz describe_collection](https://docs.zilliz.com/reference/python/python/Collections-describe_collection)、
[Milvus Full Text Search](https://milvus.io/docs/full-text-search.md)。
