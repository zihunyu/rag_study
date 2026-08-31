# 配置系统迁移完成包

状态：`REVIEW_REQUESTED`  
范围：仅配置系统迁移；其他 G1 功能保持暂停  
日期：2026-08-31

## 1. 结果

配置系统已经从旧 YAML/JSON Schema/本地敏感文件组合迁移为单一 `.env` 模型：

- 实际配置：`config/.env`，Git ignore；
- 唯一模板：`config/.env.example`，跟踪；
- `config` 目录实际内容只有这两个文件；
- 进程环境变量 > `config/.env` > 类型默认值；
- 122 个模板键全部映射到 `EnvSettings` 明确类型；
- 报告只包含变量名、类型、来源、配置状态、错误码和 Gate，不包含值；
- 旧配置引用扫描为 0（历史原始需求材料 `开发计划.md` 排除）；
- 本迁移未覆盖、重写或提交 `config/.env`。

当前 G1 配置条件仍阻断：`MINERU_TOKENS`、`AI_APPROVED_PROCESSING_REGIONS`。
这是预期的真实集成条件，不影响配置迁移本身完成。

## 2. 删除内容

### 用户配置入口

- 删除整个旧用户配置子目录，包括旧实际敏感文件、旧示例、YAML 和填写说明；
- 删除旧默认值、Gate、Schema 和 Spike 配置子目录；
- 删除旧用户配置 JSON Schema、Stub 默认值和字段 Gate 映射。

### 代码与脚本

- 删除旧 `backend/src/ragkb/config/{loader,models,validation,cli}.py`；
- 删除旧 `scripts/check_config.py`、`scripts/validate_config.py`、旧 Spike 入口；
- 收敛为 `python scripts/check_env.py`；
- 删除只测试旧 YAML/JSON Schema 的测试；
- 删除旧 MinerU 单端点路由和本地/原生向量库选择路径。

### 工件迁移

| 原位置 | 新位置 |
| --- | --- |
| CanonicalDocument Schema | `backend/src/ragkb/contracts/schemas/canonical-document-v1.schema.json` |
| G1 样本元数据 Schema | `backend/src/ragkb/contracts/schemas/g1-sample-metadata-v1.schema.json` |
| 格式样本清单 | `backend/tests/fixtures/manifests/format-samples.yaml` |

## 3. 迁移映射

| 旧配置域 | 新 `.env` 键前缀 / 结论 |
| --- | --- |
| 项目、部署、前后端 | `APP_*`、`FRONTEND_*` |
| 本地文件内容面 | `LOCAL_STORAGE_*` |
| MySQL | `MYSQL_*` |
| Redis | `REDIS_*` |
| 向量库选择与旧密钥 | 删除；固定 `ZILLIZ_CLOUD_*` |
| 外部消息队列 | 删除；固定 `QUEUE_*` Python SQLite 持久队列 |
| 单 MinerU Token | `MINERU_TOKENS` 英文逗号分隔池 |
| LLM / Embedding / Reranker / ASR | `LLM_*` / `EMBEDDING_*` / `RERANKER_*` / `ASR_*` |
| Chunk / 检索 | `CHUNK_*`、`PARENT_CHUNK_*`、`RETRIEVAL_*` |
| 上传 / 出站安全 | `UPLOAD_*`、`MALWARE_SCANNER`、`AI_*` |
| 本地身份 / OIDC | `AUTH_*`、`OIDC_*` |
| 日志 / Trace | `LOG_*`、`OTEL_*` |
| 负责人、预算等非运行治理字段 | 不再作为运行配置；由后续治理工件管理 |

`config/.env.example` 是唯一帮助文案和模板；每个变量均有用途、单位或条件说明。

## 4. 类型与条件校验

类型包括布尔、受限枚举、整数/浮点范围、Path、CSV tuple 和 `SecretStr`。主要条件：

- Queue heartbeat 必须小于 lease；
- Chunk overlap 必须小于 target；
- Embedding dimension 必须等于 Zilliz dimension；
- Zilliz URI 必须是中国区 HTTPS，安全一致性固定 Strong，BM25 必须启用；
- `restricted` 禁止出站；外部 AI 需要已批准处理区域；
- internal/confidential 使用 HTTP 时必须同时配置受信任服务名和可审计内网/VPN 加密证据；
- OIDC、OTel 和生产签名密钥按启用条件要求；
- 未配置/占位值使用类型默认继续本地运行，但配置状态保持未配置并按 Gate 阻断。

类型转换错误只报告变量名和错误类型，不返回输入值。

## 5. 中间件、Zilliz 与队列

- 中间件只保留 MySQL 与 Redis；
- 任务队列固定为 Python 本地 SQLite 持久队列；
- 配置、代码、文档和计划中不存在其他消息队列路径；
- 检索部署固定为 Zilliz Cloud 中国区；
- `ZillizCloudAdapter` 使用 `pymilvus.MilvusClient(uri, token, db_name, timeout)`；
- 单测通过注入 factory 验证参数契约，未连接真实集群，状态不含 Token。

## 6. MinerU 多 Token 池

`MINERU_TOKENS` 解析为 `SecretStr` tuple。`MinerUTokenPool` 已实现：

- round-robin；
- 单 Token 最大并发；
- 连续失败计数；
- 429 立即冷却；
- 连续失败达阈值冷却；
- 自动故障切换；
- 全部 Token 冷却/占用时返回 `MINERU_TOKENS_UNAVAILABLE` 可重试错误；
- 状态只含 slot、并发、失败数和冷却布尔值；
- 异常、状态、repr 和测试输出不包含 Token。

5 项专用测试覆盖轮询、并发上限、429 切换、连续失败冷却和并发竞争，未调用真实服务。

## 7. 测试证据

命令：`& '.\.venv\Scripts\python.exe' scripts/run_quality.py`

| 检查 | 结果 |
| --- | --- |
| Python | 3.12.13 |
| Ruff lint / format | PASS；92 files |
| mypy strict | PASS；49 source files |
| pytest | PASS；66 tests |
| npm check | PASS |
| 后端 / Worker / MinerU / SQLite migration | PASS |
| OpenAPI snapshot | PASS |
| 技术 Harness | PASS；真实 acceptance 仍 false/BLOCKED |
| 无 Docker 扫描 | PASS |
| 静态密钥扫描 | PASS；0 findings；不扫描 `config/.env` |
| 失败 / 跳过 | 0 / 0 |

额外验收：

- `git check-ignore -v --no-index config/.env`：PASS；
- `Get-ChildItem config -Force`：仅 `.env`、`.env.example`；
- 旧配置/旧密钥/旧消息队列引用计数：0；
- `.env.example` 的全部 Secret 键均被测试判为未配置占位符；
- `config/.env` 未跟踪、未覆盖、未输出值。

## 8. 仍待用户填写的键

### G1

- `MINERU_TOKENS`
- `AI_APPROVED_PROCESSING_REGIONS`

### G2

- `MYSQL_HOST`、`MYSQL_USER`、`MYSQL_PASSWORD`
- `ZILLIZ_CLOUD_URI` 已配置但未通过中国区 HTTPS Endpoint 校验，需要修正；`ZILLIZ_CLOUD_DIMENSION` 待填
- `EMBEDDING_BASE_URL`、`EMBEDDING_API_KEY`、`EMBEDDING_MODEL`、`EMBEDDING_DIMENSION`
- `RERANKER_BASE_URL`、`RERANKER_API_KEY`、`RERANKER_MODEL`
- `ASR_BASE_URL`、`ASR_API_KEY`、`ASR_MODEL`

### G3

- `REDIS_HOST`
- `LLM_BASE_URL`、`LLM_API_KEY`、`LLM_MODEL`

生产/OIDC/OTel 只在启用对应模式时要求。真实值只填写 `config/.env`，不要发送到聊天或报告。

## 9. 暂停与提交边界

迁移保持未提交，未继续其他 G1 功能，也未申请 G1 Gate。审核完成前不提交本迁移、不恢复
其他 G1 开发。

CONFIG_MIGRATION_REVIEW_REQUESTED
