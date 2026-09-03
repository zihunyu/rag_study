# G0 ADR 登记册

配置 revision：`g0-adr-register-v3`。任何本地 Stub 实现都不代表目标组件验收。
本登记册完成 G0 技术方向重基线；真实组件能力仍由对应 G1—G4 Gate 验收。

配置迁移补充决策：实际配置仅 `config/.env`，唯一模板仅 `config/.env.example`；进程环境
优先。中间件只允许 MySQL/Redis，队列固定 SQLite，检索固定 Zilliz Cloud 中国区，
MinerU 使用多 Token round-robin 池。旧 YAML 配置、外部消息队列和本地向量库选择均已删除。

| ADR | 议题 | 当前状态 | 用户配置路径 / 退出证据 |
| --- | --- | --- | --- |
| ADR-001 | 模块化单体与独立 Worker | Migrated | 队列固定为 Python 本地 SQLite 持久队列，不提供其他队列适配器 |
| ADR-002 | MySQL/Redis 控制与缓存、本地文件内容面、Zilliz Cloud 检索投影 | Migrated | 中间件只保留 MySQL/Redis；检索部署固定 Zilliz Cloud 中国区 |
| ADR-003 | 不可变文档版本与发布指针 | Accepted direction | G1 状态机与并发测试 |
| ADR-004 | 高水位、增量追平、索引代际和回滚窗口 | Accepted direction | G2 真实 Zilliz Cloud 与恢复预算硬验收 |
| ADR-005 | Transactional Outbox、至少一次消费、Inbox 去重 | Accepted direction | G1 SQLite 持久队列与后续 MySQL 事务兼容验证 |
| ADR-006 | CanonicalDocument 隔离 MinerU 私有结构 | Accepted direction | G0 冻结六类 60 槽位清单和 Harness；G1/G2 只验契约、适配器与合成 Fixture，六类真实格式统一在 G4 硬验收 |
| ADR-007 | Zilliz Cloud 中国区原生 BM25 first、应用层 RRF | Migrated | `pymilvus.MilvusClient` URI+Token；G2 验证中文 Analyzer、ARRAY ACL、watermark、质量和 p95 |
| ADR-008 | 预编译 ACL、security watermark、deny-first | Accepted direction | G2 ARRAY ACL/水位真实硬验收 |
| ADR-009 | `/search` 与 `/ask` 分离、后端生成引用定位 | Accepted direction | G2/G3 契约验收 |
| ADR-010 | 确定性 RAG，不采用 Agentic RAG | Accepted direction | G3 安全与回答状态验收 |
| ADR-011 | SSE 仅发进度，验证后发答案 | Accepted direction | G3 契约和安全输出验证 |
| ADR-012 | 四类 Profile 与不可变 Release Manifest | Accepted direction | G2/G3 revision 与回滚验收 |

## 无容器本地开发与目标契约边界

- 开发期后端、Worker、迁移和 Harness 都是原生 Python 进程；前端由 npm 脚本运行。
- 文件内容面使用 `./data/storage`，写入时按分区校验路径并采用临时文件加原子替换。
- 中间件只保留 MySQL 与 Redis；队列固定为 Python 本地 SQLite 持久实现。
- Zilliz Cloud 中国区是唯一检索部署；底层 Milvus 兼容 Analyzer/BM25/ARRAY ACL/watermark
  能力在 G2 真实验证，未执行前不得通过相应 Gate。

## 范围重基线决定

Excel/CSV 与 WAV/MP3/M4A 已并入 R1。基线为 24 周、约 270 人周；六类真实格式
统一在 G4 执行硬门禁，G1/G2 只验契约、解析器、适配器、合成 Fixture 与 Harness，
发布黄金集为 230 份起。
