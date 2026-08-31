# 用户配置填写说明

如果你的目标是启动服务和测试真实接口，请先阅读 [`运行配置填写说明.md`](./运行配置填写说明.md)。它直接说明 MySQL、Milvus、LLM、Embedding、Reranker、MinerU、ASR、RabbitMQ 和 Redis 分别写在哪个文件、如何填写。

负责人、预算、数据保留等生产治理字段见 [`字段填写指南.md`](./字段填写指南.md)，但这些字段不能阻止本地服务启动或普通开发测试。

开发任务会优先读取本目录。缺失配置不会阻止可以使用 Stub、本地 Python 适配器或本地假服务完成的工程工作；但达到对应 Gate 前必须补齐阻断项。

## 用户已确定的强制约束

- 禁止 Docker、Docker Compose、Testcontainers 和任何容器化启动方式；
- 后端、Worker、迁移和辅助工具使用 `python xxx.py` 启动；
- 前端使用 `npm run dev`；
- 原件、解析产物、媒体、隔离区和临时文件使用项目本地文件系统，默认根目录 `./data/storage`；
- MySQL、Milvus、RabbitMQ、Redis 只能使用本机原生服务、已有原生服务或 Python 本地/嵌入式适配器。

## 请先填写

1. 按 `字段填写指南.md` 第八节，用中文提供非敏感信息；不要求你直接编辑 YAML。
2. 只在 `.env.user.local` 中填写 Token。该文件已加入 `.gitignore`，不要复制到聊天、日志或提交记录。
3. 模型维度、版本、并发、端口、路径和架构审批由开发任务探测或审核后回填，不要求用户猜测。
4. 无法确定的业务字段保留 `__FILL_ME__`。
5. 开发任务不得把 `.env.user.local` 的值输出到终端、测试快照、日志或审核报告。
6. 校验报告按 `user`、`service_probe`、`technical_review`、`system_default` 标记责任人，
   并把 Basic/Stub 启动与 Real Integration/E2E 分开报告。

## 启动与测试优先级

### 本地 Stub 启动

- 使用项目已有本地路径和 Python Stub；
- 不要求 MySQL、Milvus、第三方模型或 Token；
- 不要求负责人、年度容量、保留期限或预算。

### 真实文字 RAG 测试

- LLM、Embedding、Reranker 的 Base URL、精确 model ID 和 Token；
- MinerU 托管地址和 Token，或者自建 MinerU 地址；
- MySQL、Milvus 选择真实服务或明确使用本地适配器；
- 模型维度、版本、并发和超时由开发任务探测或回填。

### 真实音频 RAG 测试

- 在文字 RAG 配置之外，填写 ASR Base URL、model ID 和 Token。

### 生产发布前再填写

- 负责人、法律保留、保存天数和预算；
- 生产 HA、WORM、告警和值班；
- 域名、证书、密钥管理和合规审批；
- 正式迁移窗口和支持 SLA。

## 不影响开发的默认策略

- 缺真实服务时使用接口 Stub、本地 Python Fake 或临时目录 Fixture；
- 缺模型密钥时使用确定性 Fake Adapter，不伪造真实质量结果；
- 缺 OIDC 时开发环境使用单用户测试身份；
- 缺生产参数时只能完成开发和单元/集成测试，不能通过相应 Gate；
- 任一 `__FILL_ME__` 是否阻断，由阶段审核报告按 Gate 明确列出。
