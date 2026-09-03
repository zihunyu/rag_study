# G4 监控与告警契约

| 信号 | 级别 | 本地动作 | 真实动作边界 |
| --- | --- | --- | --- |
| permission watermark 落后 | critical | fail-closed，停止检索/发布 | 不自动改 Zilliz |
| tombstone 与可见性冲突 | critical | 隐藏资源，运行本地对账 | 不自动云端删除 |
| publication checksum/generation mismatch | high | 409，旧版继续服务 | 不自动 swap |
| cleanup postcondition 失败 | high | FAILED，可安全重试 | 外部存储保持 PENDING_APPROVAL |
| ASR/OCR/Office stub 被使用 | warning | 标记 BLOCKED_REAL_VALIDATION | 不宣称真实格式支持 |
| cost breaker OPEN | warning | 拒绝后续请求，记录 Dry-run | 不发送计费探针 |
| backup restore tombstone 未先重放 | critical | 中止恢复 | 不触碰真实备份 |

告警载荷只允许资源 opaque ID、revision、状态和计数，不包含正文、Token 或密钥。
