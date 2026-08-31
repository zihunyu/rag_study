# G0 ADR 登记册

配置 revision：`g0-adr-register-v3`。任何本地 Stub 实现都不代表目标组件验收。
本登记册完成 G0 技术方向重基线；真实组件能力仍由对应 G1—G4 Gate 验收。

| ADR | 议题 | 当前状态 | 用户配置路径 / 退出证据 |
| --- | --- | --- | --- |
| ADR-001 | 模块化单体与独立 Worker；队列实现可替换 | Accepted | FastAPI/模块化单体；R1 默认 SQLite/文件日志支持的 Python 本地持久队列；RabbitMQ/Celery 仅为可选原生适配器 |
| ADR-002 | MySQL 控制面、本地文件内容面、Milvus 检索投影 | Accepted direction | MySQL 与本地文件事实源均 `approve`；本地 MySQL 由 Stub 替代，真实兼容待集成 |
| ADR-003 | 不可变文档版本与发布指针 | Accepted direction | G1 状态机与并发测试 |
| ADR-004 | 高水位、增量追平、索引代际和回滚窗口 | Accepted direction | G2 真实 Milvus 与恢复预算硬验收 |
| ADR-005 | Transactional Outbox、至少一次消费、Inbox 去重 | Accepted direction | G1 本地持久队列，后续 MySQL/可选 MQ 兼容验证 |
| ADR-006 | CanonicalDocument 隔离 MinerU 私有结构 | Accepted direction | G0 冻结六类 60 槽位清单和 Harness；前五类 G1、音频 G2 执行真实硬门禁 |
| ADR-007 | 生产目标 Milvus 2.6 原生 BM25 first、应用层 RRF | Accepted | G1 本地词法适配器只供开发；G2 必须验证真实中文 Analyzer、ARRAY ACL、watermark、质量和 p95 |
| ADR-008 | 预编译 ACL、security watermark、deny-first | Accepted direction | G2 ARRAY ACL/水位真实硬验收 |
| ADR-009 | `/search` 与 `/ask` 分离、后端生成引用定位 | Accepted direction | G2/G3 契约验收 |
| ADR-010 | 确定性 RAG，不采用 Agentic RAG | Accepted direction | G3 安全与回答状态验收 |
| ADR-011 | SSE 仅发进度，验证后发答案 | Accepted direction | `adr_approvals.sse_progress_verified_answer_only=approve` |
| ADR-012 | 四类 Profile 与不可变 Release Manifest | Accepted direction | G2/G3 revision 与回滚验收 |

## 无容器本地开发与目标契约边界

- 开发期后端、Worker、迁移和 Harness 都是原生 Python 进程；前端由 npm 脚本运行。
- 文件内容面使用 `./data/storage`，写入时按分区校验路径并采用临时文件加原子替换。
- MySQL、Milvus、可选 RabbitMQ、Redis 的生产契约未删除。当前缺本机原生服务时，分别由端口后的
  Stub/本地适配器替代；默认任务队列使用 Python 本地持久实现。这只能验证调用契约和失败语义。
- 兼容性退出条件包括：原生服务版本矩阵、真实连接/错误语义、并发与恢复、Milvus 中文
  Analyzer/BM25/ARRAY ACL/watermark、MySQL 事务与 Outbox。未执行前 Gate 保持阻断。

## 范围重基线决定

`adr_approvals.r1_includes_spreadsheets_audio=approve`：Excel/CSV 与 WAV/MP3/M4A 已并入
R1。基线为 24 周、约 270 人周；G0 冻结 60 槽位采集清单，前五类 G1、音频 G2
执行真实硬门禁，发布黄金集为 230 份起。
