# ADR 0004：Production 使用 MySQL Retrieval Projection 与 Release 水位

状态：Accepted

Production 不得调用 SQLite 的测试写入接口。Chunk 展示文本、ACL、分类、有效期、生命周期与
当前版本状态写入 MySQL `retrieval_chunk_projections`；可查询代际、权限修订和安全水位来自
`retrieval_release_state`。Zilliz/Milvus 只负责候选召回，MySQL 再次授权，应用内生命周期状态
执行最终防线。发布、回滚、撤权、ACL 转换和删除同步更新 MySQL 与向量投影，任一外部写入失败
时本地生命周期不得变为可见。
