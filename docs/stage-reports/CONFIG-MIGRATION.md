# 配置系统迁移完成包

状态：`APPROVED_AND_COMMITTED`

范围：配置系统迁移及后续 Gate 策略修订
日期：2026-08-31

## 1. 结果

配置系统已经从旧 YAML/JSON Schema/本地敏感文件组合迁移为单一 `.env` 模型：

- 实际配置：`config/.env`，Git ignore；
- 唯一模板：`config/.env.example`，跟踪；
- `config` 目录实际内容只有这两个文件；
- 进程环境变量 > `config/.env` > 类型默认值；
- 123 个模板键全部映射到 `EnvSettings` 明确类型；
- 报告只包含变量名、类型、来源、配置状态、错误码和 Gate，不包含值；
- 旧配置引用扫描为 0（历史原始需求材料 `开发计划.md` 排除）；
- 本迁移未覆盖、重写或提交 `config/.env`。

用户已在本机实际配置中补齐 G1 字段；安全报告只验证状态，不记录或输出配置值。

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
| G4 全格式样本元数据 Schema | `backend/src/ragkb/contracts/schemas/format-sample-metadata-v1.schema.json` |
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
- `LLM_ALLOW_HTTP=true` 时 LLM 可使用 HTTP/HTTPS；为 `false` 时要求 HTTPS 或受信任私有传输服务与证据；
- 其他 external AI 服务对 internal/confidential 使用 HTTP 时，必须同时配置受信任服务名和可审计内网/VPN 传输证据；
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
| Ruff lint / format | PASS；221 files |
| mypy strict | PASS；93 source files |
| pytest | PASS；214 tests |
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

## 8. 后续 Gate 配置边界

### G2

- `MYSQL_HOST`、`MYSQL_USER`、`MYSQL_PASSWORD`
- `ZILLIZ_CLOUD_URI`、`ZILLIZ_CLOUD_TOKEN`、`ZILLIZ_CLOUD_DIMENSION`；URI 必须为中国区 HTTPS Endpoint
- `EMBEDDING_BASE_URL`、`EMBEDDING_API_KEY`、`EMBEDDING_MODEL`、`EMBEDDING_DIMENSION`
- `RERANKER_BASE_URL`、`RERANKER_API_KEY`、`RERANKER_MODEL`

### G3

- `REDIS_HOST`
- `LLM_BASE_URL`、`LLM_API_KEY`、`LLM_MODEL`

### G4

- `ASR_ENABLED=false` 为当前默认范围；ASR 三键仅在重新启用时要求。
- 原始完整格式范围为六类 6x10；当前非 ASR 范围只计算文本 PDF、扫描/图片、
  DOCX、PPTX、表格五类 5x10。audio 标记 `deferred_by_user`。
- `APP_SECRET_KEY` 已配置，报告只验证 configured 状态。

G1/G2 的格式工作只验证契约、解析器、适配器、合成 Fixture 与 Harness，
`real_acceptance=false`，不得据此声明真实格式支持。

生产/OIDC/OTel 只在启用对应模式时要求。真实值只填写 `config/.env`，不要发送到聊天或报告。

## 9. 提交边界

配置迁移已审核并提交为 `b1f119b`。后续策略修订当前保留为未提交改动；用户已暂停
commit/merge/rebase/tag/push/PR。`config/.env` 始终被 Git 忽略，未被覆盖、读取到报告或提交。

CONFIG_MIGRATION_APPROVED
