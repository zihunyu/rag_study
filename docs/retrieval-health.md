# 检索健康状态与问答结果

检索通道是否成功独立于是否命中内容。`SearchResult` 将实际成功完成的通道区分为
`healthy`、`degraded` 或 `unavailable`，证据构建器将其和全部降级原因保存到
`EvidencePackage.retrieval_health`、`EvidencePackage.retrieval_warnings`。

| 检索情况 | 问答结果 | verified | retryable |
| --- | --- | --- | --- |
| 正常检索，无有效证据 | insufficient_evidence | true | false |
| 两个检索通道都失败 | system_error | false | true |
| 部分检索失败，剩余通道无证据 | system_error | false | true |
| 部分检索失败，仍有有效证据 | 继续生成、验证及权限复核 | 仅验证通过才为 true | false |

失败通道与正常返回空命中的通道分别统计。原生混合检索失败后，如果 BM25 回退成功，
即使返回零命中，也属于降级；回退再次失败才属于不可用。重排故障使用已有顺序继续，
并保留 `RERANKER_UNAVAILABLE`。

普通问答和 SSE 的最终结果都增加 `retrieval_health`、`degraded`、`retryable`。
`warnings` 合并检索原因与生成/验证等后续阶段的原因。完全不可用使用
`RETRIEVAL_UNAVAILABLE`，降级且无证据使用 `RETRIEVAL_INCOMPLETE`，二者都不调用
生成器，不返回答案或引用。接口沿用 HTTP 200 返回业务结果的约定，客户端按
`status=system_error` 和 `retryable=true` 判断是否可重试；SSE 使用相同结果字段。

缓存仅保存已验证的答案草稿。每次请求的健康状态来自本次检索，缓存命中不会清除
降级标记，服务恢复后也不会沿用上一次故障的标记。

SQLite 和 MySQL 的证据包、结果 JSON 都持久化这些字段，无需数据库表迁移。
读取旧记录时使用默认值以兼容既有数据；这些默认值不能用于证明历史检索是否健康。
