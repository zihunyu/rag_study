# RAG 知识库本地开发执行计划与配置填写指南

> 适用范围：Windows 本机开发、禁止 Docker、后端以 `python xxx.py` 启动、前端以 `npm run dev` 启动、文件保存在项目本地目录。
>
> 本文是 `完整开发计划.md` 的本地执行补充，不替代其中的企业生产验收要求。

## 1. 已确认的约束和当前环境

### 1.1 已确认约束

- 不使用 Docker、Docker Compose 或 Kubernetes 启动开发环境。
- 后端 Web 服务使用 `python run_backend.py` 启动。
- 异步任务如需独立进程，使用 `python run_worker.py` 启动。
- Vue 3 前端使用 `npm run dev` 启动。
- 原文件、解析产物、索引文件、日志和本地开发数据库保存在项目的 `data/` 目录。
- `.env.user.local` 只保存密码、Token 和 API Key，不提交到版本库，也不复制到聊天或日志。

### 1.2 本机检查结果

| 项目 | 当前结果 | 对开发的影响 |
| --- | --- | --- |
| 操作系统 | Windows 11 专业版 64 位 | 采用 Windows 原生启动脚本 |
| Python | `python` 为 3.9.18，`py` 可见 3.10 | 项目要求 Python 3.12，开始编码前需安装 3.12 |
| Node.js / npm | Node 22.14.0 / npm 10.9.2 | 可以用于 Vue 3 开发 |
| 内存 | 约 32 GB | 适合本地小规模开发与评测，不按生产容量压测 |
| GPU | NVIDIA GeForce RTX 4070，约 12 GB 显存 | 可尝试本地 OCR、Embedding 或小模型；是否使用由模型配置决定 |
| MySQL / Milvus / Redis / RabbitMQ | 未发现本机服务或常用端口监听 | 当前不能直接填写可用服务地址 |

## 2. 一个必须先说明的技术限制

当前项目目标使用 MySQL、Milvus、Redis 和 RabbitMQ，但“Windows 原生 + 禁止 Docker + 严格本地存储”不能直接满足原计划的全部组件基线：

- Milvus Lite 官方当前仅列出 Ubuntu 和 macOS，不支持 Windows 原生环境。
- Milvus Lite 适合小规模本地检索，但内置 BM25 全文检索当前不在 Milvus Lite 中提供。
- 因此，不能把 `milvus.uri` 随便填成一个本地路径并宣称已经满足 Milvus 2.6.x + 原生 BM25 的验收要求。

本地开发建议采用以下策略：

1. **推荐：本地适配器。** 开发阶段以 SQLite 保存控制面数据，以本地文件向量索引和应用层 BM25 完成纵向功能；所有存储都通过接口适配器访问，后续再替换为 MySQL、Milvus、Redis 和 RabbitMQ。该方案满足“先在本机跑起来”，但不能通过生产组件集成 Gate。
2. **可选：WSL2。** 后端在 Ubuntu WSL2 中以 `python run_backend.py` 启动，并使用 Milvus Lite；BM25 仍需应用层实现。它不使用 Docker，但增加 Windows/WSL 路径和运维复杂度。
3. **可选：外部 Milvus。** 使用内网或云端 Milvus。此方案不再属于严格本地存储，并涉及数据出站和真实服务配置，必须由用户明确批准。

在你没有选择前，执行计划按第 1 种方案推进，并在所有报告中标记“本地开发适配器，不代表生产 Milvus 验收通过”。

### 2.1 已收到的用户配置决定

- 正式业务域暂未确定，本地试点暂命名为“通用企业文档知识库”，首个技术知识空间使用 `general_knowledge`。
- R1 直接覆盖常见办公文档、PDF、图片、HTML、Excel/CSV 和音频。
- 数据包含个人信息或敏感内容，按 `confidential` 基线保护并启用 PII/DLP 要求。
- 用户已明确允许真实资料发送到已批准的第三方 AI 服务；`public`、`internal` 和 `confidential` 数据允许出站，`restricted` 仍禁止。
- LLM、Embedding 和 Reranker 采用第三方 Base URL + Token，并必须按供应商隔离密钥、超时、限流和审计。
- MinerU 同时实现托管 API 和自建 API 适配器，托管端点已启用，自建端点保留为回退方案。
- 跨境传输仍未自动放开；第三方服务投入使用前必须确认数据处理区域和合规条款。
- 自建 MinerU 使用 Windows 原生 Python 安装和本地 API，不使用 Docker；项目提供 `python run_mineru.py` 包装入口。
- 初始规模为 3,000 份文档、每日新增或更新 200 份、50 个并发问答请求。
- 本地版采用单用户测试身份，不接企业登录。
- 预算币种为人民币（CNY），具体金额仍待填写。

## 3. 本地目录与启动方式

建议固定以下结构，避免把运行数据散落在源码目录：

```text
data/
├── original/          # 用户上传的原文件
├── artifacts/         # 规范化文档、OCR、缩略图等产物
├── quarantine/        # 隔离文件
├── database/          # 本地开发数据库
├── vector/            # 本地向量索引
├── cache/             # 可删除的缓存
├── audit/             # 本地审计日志
└── backups/           # 本地开发备份
```

目标启动命令：

```powershell
# 后端 API
python run_backend.py

# 独立任务进程；初期若使用进程内任务则不启动
python run_worker.py

# 自建 MinerU；启用本地解析服务时启动
python run_mineru.py

# 前端
cd frontend
npm run dev

# 配置校验
python scripts/check_config.py
```

启动脚本负责检查目录、端口和缺失配置，但不得自动下载大模型、申请外部资源或把密钥打印到终端。

## 4. 分阶段开发计划

### G0：配置与契约冻结

目标：先消除“开发环境到底用什么”的歧义。

- 冻结本地运行约束、目录、端口和启动命令。
- 决定本地开发适配器或 WSL2 路线。
- 确定 LLM、Embedding、Reranker 和 MinerU 使用本地模型还是外部 API。
- 将配置拆成非敏感 YAML、敏感 `.env.user.local` 和程序默认值。
- 为所有配置做类型校验和启动前错误提示。

退出条件：最小配置能通过校验；未选择的生产服务明确标记为 `deferred`，而不是伪造地址。

### G1：工程骨架与本地存储

- 建立 Python 3.12 虚拟环境、依赖锁定和 Vue 3 工程。
- 实现 `python run_backend.py`、可选 `python run_worker.py`、`python run_mineru.py` 和 `npm run dev`。
- 建立配置加载、健康检查、统一错误、日志和 Trace ID。
- 实现本地文件存储、SQLite 控制面、本地缓存和进程内任务适配器。
- 冻结文档、版本、Chunk、证据和回答状态契约。

退出条件：前后端可启动；健康检查通过；上传文件可安全写入 `data/quarantine/`。

### G2：文档入库纵向链路

- 实现上传、哈希去重、类型识别和原件保留。
- 先支持 Markdown、TXT、文本 PDF，再逐步接入扫描 PDF、图片、DOCX 和 PPTX。
- 实现统一文档结构、清洗、Chunk、质量检查和失败重试。
- 保存页码、章节、坐标或时间戳等可追溯定位。
- 开发任务状态和文档发布状态分离，避免半成品进入检索。

退出条件：黄金样本文档可从上传走到可发布 Chunk；失败样本可见、可重试且不污染索引。

### G3：本地检索与索引

- 实现 Embedding 适配器和维度校验。
- 使用本地文件向量索引完成 Dense 召回。
- 使用应用层 BM25 完成关键词召回。
- 实现相同 ACL/版本/有效期过滤、RRF、去重和 Reranker。
- 为未来 MySQL/Milvus 适配器编写契约测试，禁止业务代码直接依赖本地存储实现。

退出条件：`/search` 在固定评测集上可重复运行；越权样本零泄漏；检索结果能回到原文位置。

### G4：可信问答与 Vue 前端

- 实现 `/ask`、证据包、上下文预算、回答状态和引用校验。
- 无证据、证据冲突、条件缺失和模型失败时安全降级。
- 实现登录占位、知识空间、上传、任务中心、搜索、聊天和来源预览页面。
- SSE 只推送阶段进度，验证完成后再显示答案正文。

退出条件：问答只能引用已授权且当前有效的证据；前端能完整展示答案状态和来源。

### G5：治理、安全、评测与本地试点

- 实现版本更新、回滚、撤权、删除和索引清理。
- 增加恶意文件、提示词注入、路径穿越、越权和密钥泄漏测试。
- 建立解析、检索、答案、引用、时延和费用评测报告。
- 编写用户手册、配置手册、故障排查和备份恢复说明。

退出条件：本地试点连续运行；无 P0/P1 缺陷；本地备份与恢复演练通过。

### G6：目标组件集成与生产化（当前条件下暂缓）

- 集成 MySQL、Milvus、Redis、RabbitMQ、企业 OIDC 和生产密钥管理。
- 运行真实容量、HA、RPO/RTO、安全、合规和成本验证。
- 完成生产灰度、回滚和正式验收。

阻断说明：如果始终要求 Windows 原生、禁止 Docker、禁止 WSL2、禁止外部服务，则无法完成原计划的 Milvus 目标组件验收，G6 必须保持阻断或重新批准技术选型。

## 5. `project-inputs.yaml` 怎么填

### 5.1 填写原则

- 你只填写业务选择、数据是否允许出站、使用哪个模型服务、预算等“必须由人决定”的字段。
- 端口、模型维度、版本号等可以从服务或模型自动读取的字段，不要凭感觉填写。
- 当前不知道的字段保留 `__FILL_ME__`，并在 `notes` 写“谁决定、预计何时决定”。
- 本地开发不使用的生产字段填 `deferred` 或 `not_applicable`；程序校验器应识别这些状态。
- 路径在 YAML 中优先使用正斜杠，例如 `E:/Data/codex/20260831rag/data`，减少反斜杠转义问题。

### 5.2 你现在需要填写的字段

| 字段 | 应填写什么 | 不知道时怎么处理 |
| --- | --- | --- |
| `project.pilot_business_domain` | 第一批知识属于哪个业务，如“公司制度”或“产品技术文档” | 填最先试点的一个领域，不要写“全部” |
| `project.initial_tenant_code` | 租户短代码，如 `demo` 或公司英文简称 | 单人本地开发可用 `demo` |
| `project.initial_knowledge_space` | 首个知识空间名，如 `company_policies` | 可用英文小写加下划线 |
| `project.owners.*` | 产品、技术、安全、运维、QA、业务验收负责人 | 一人项目可暂时都填同一姓名，并在 `notes` 标记“暂代” |
| `scope.*` | R1 必须支持的格式 | 不需要的格式应从 R1 移到后续版本，减少首版风险 |
| `deployment.air_gapped` | 是否完全不能访问公网 | 使用任何外部模型 API 时必须为 `false` |
| `capacity.*` | 首批文档数、日增量、并发和单文件上限 | 没有统计时先用本地示例的小规模值，试导入后再改 |
| `ai_services.outbound_ai_allowed` | 数据是否允许发往外部模型服务 | 不确定就填 `false`，安全默认拒绝出站 |
| `ai_services.*.provider` | 实际使用的服务商或 `local` | 不能填“随便”或不存在的服务名 |
| `ai_services.*.model_id` | 服务商控制台或本地模型的精确 ID | 必须从服务实际返回值复制，不要猜 |
| `security_compliance.highest_classification_in_scope` | 本地试点数据最高密级 | 未确认前按 `internal` 或更严格处理 |
| `security_compliance.pii_dlp_required` | 是否包含个人信息并需要检测/脱敏 | 只要可能含姓名、电话、身份证等就填 `true` |
| `slo_and_finops.*` | 响应、恢复和费用上限 | 本地开发值只是目标，不等于生产承诺 |
| `evaluation_business.top_user_tasks` | 用户最常问的 3—5 类问题 | 用真实业务句子填写，可直接变成验收题 |
| `adr_approvals.*` | 对关键架构决策填 `approve/reject/revise` | 不懂时填 `revise` 并写明问题，不要盲目批准 |

### 5.3 本地开发建议值

以下值适合“先跑通”的本地基线，不是生产容量承诺：

```yaml
deployment:
  mode: local_dev
  target_platform: native_processes
  docker_forbidden: true
  local_storage_only: true
  air_gapped: false  # 只有确实使用外部 API 时才这样填
  production_region: local_machine
  allowed_data_regions: [local_machine]
  gpu_available: true
  gpu_model_and_count: "NVIDIA GeForce RTX 4070 12GB x1"

capacity:
  initial_document_count: 1000
  initial_chunk_count: 50000
  daily_new_or_updated_documents: 50
  annual_growth_percent: 20
  peak_search_qps: 2
  concurrent_ask_requests: 3
  max_file_size_mb: 100
  max_pages_per_document: 300
  average_pages_per_document: 10
  average_chunk_tokens: 500
```

这些容量值只是为了防止本地开发一开始按 10 万文档、500 万 Chunk 配置资源。拿到真实样本统计后必须重新估算。

### 5.4 基础设施字段的本地填写提示

| 模块 | 本地建议 | 说明 |
| --- | --- | --- |
| MySQL | `python_dev_stub` | 本地先用 Python 开发适配器；若安装 MySQL 8.4 Windows 服务，再改为 `native_local`、`127.0.0.1:3306` |
| Milvus | `python_embedded_or_stub` | Windows 原生本地版先用文件向量索引；不得伪填 `localhost:19530` |
| 本地文件存储 | `infrastructure.local_storage.*` | 默认根目录 `./data/storage`，原件、产物、隔离区、审计和备份使用独立子目录 |
| RabbitMQ | `python_local_adapter` | 本地先用进程内任务或数据库任务表；真实队列留到集成阶段 |
| Redis | `python_local_adapter` | 本地使用内存或磁盘缓存；缓存必须可删除、可重建 |
| OIDC | `other` + 本地测试身份 | 只用于开发；不得当作生产鉴权验收通过 |

当前 `project-inputs.yaml` 已加入本地模式字段；复制参考值后仍应运行配置校验，因为开发任务可能继续收紧字段枚举和 Gate 规则。

## 6. AI 服务怎么填

AI 配置最容易填错。每种能力分别确定，不能用一个“AI 地址”代替全部服务：

| 能力 | 必须知道 | 可以自动获取/验证 |
| --- | --- | --- |
| MinerU | 本地或远程 API | 安装版本、健康状态、支持格式 |
| LLM | provider、endpoint、精确 model ID、API Key | revision、超时可从探测结果调整 |
| Embedding | provider、endpoint、精确 model ID、API Key | dimension 必须调用模型或查模型元数据确认 |
| Reranker | provider、endpoint、精确 model ID、API Key | 最大批量、并发和超时通过压测确定 |
| OCR | 使用 MinerU 还是独立 OCR | 独立服务才需要 `OCR_API_KEY` |
| ASR | R1 音频所需的 provider、endpoint 和具体模型 | 用户提供服务信息，技术探测超时与并发 |

`embedding.dimension` 绝不能猜。索引维度必须与模型输出一致；更换 Embedding 模型后需要建立新的索引代际。

如果使用外部 API：

- `outbound_ai_allowed` 必须与安全决定一致；
- `allowed_data_classifications` 只列允许发送的密级；
- endpoint 填 API 基础地址，不要把 Key 拼在 URL 中；
- Key 只放 `.env.user.local`；
- 免费额度、个人账号或临时代理不能默认视为生产可用服务。

## 7. `.env.user.local` 怎么填

只填写当前真正启用的服务。未启用的字段保持未决状态，不要编造 Token。

| 环境变量 | 什么时候必须填 |
| --- | --- |
| `MYSQL_PASSWORD` | 使用真实 MySQL 时 |
| `MILVUS_TOKEN` | 使用需要认证的 Milvus 服务时；本地适配器不需要 |
| `S3_ACCESS_KEY` / `S3_SECRET_KEY` | 使用 S3/MinIO 时；本地文件系统不需要 |
| `RABBITMQ_URL` | 使用真实 RabbitMQ 时；进程内任务不需要 |
| `REDIS_URL` | 使用真实 Redis 时；本地缓存不需要 |
| `OIDC_CLIENT_SECRET` | 接入企业 OIDC/Keycloak 客户端时 |
| `MINERU_TOKEN` | 使用 MinerU 远程 API 时 |
| `LLM_API_KEY` | 使用需要 Key 的 LLM 服务时 |
| `EMBEDDING_API_KEY` | 使用需要 Key 的 Embedding 服务时 |
| `RERANKER_API_KEY` | 使用需要 Key 的 Reranker 服务时 |
| `OCR_API_KEY` | OCR 独立于 MinerU 且服务需要 Key 时 |
| `ASR_API_KEY` | R1 启用外部语音转写时 |

密钥填写完成后只需要告诉开发任务“哪些变量已经配置”，不要把值发到聊天中。

## 8. 你只需要先回答的 8 个问题

如果你不想直接编辑 YAML，可以先用中文回答下面问题，由开发任务映射到配置字段：

1. 第一批要导入什么业务资料？
2. 第一版必须支持哪些文件格式？
3. 资料是否包含个人信息、商业机密或受限数据？
4. 资料是否允许发送到公网 AI 服务？
5. 你已经购买或可用的 LLM、Embedding、Reranker、MinerU/OCR 服务分别是什么？
6. 首批大约多少份文档、每天新增多少、预计同时多少人提问？
7. 本地版是否需要真实登录，还是先用单用户测试身份？
8. 每月可接受的 API/模型预算是多少，使用什么币种？

回答后，仍然无法自动确定的字段只应包括真实服务地址、账号密钥和生产合规参数。

## 9. 配置完成检查表

- [ ] `python` 已切换为 3.12，并在项目虚拟环境内运行。
- [ ] YAML 中不包含密码、Token 或 API Key。
- [ ] `.env.user.local` 已被 `.gitignore` 排除。
- [ ] 所有本地路径都在项目 `data/` 范围内。
- [ ] 不存在伪造的 `localhost` 服务地址。
- [ ] Embedding 维度来自真实模型输出或官方元数据。
- [ ] 外部 AI 出站和允许的数据密级已经明确。
- [ ] 所有 `deferred` 项都对应一个后续 Gate 和负责人。
- [ ] 配置校验器把空值和 `__FILL_ME__` 都判定为未配置，并按当前模式跳过无关的 S3 等密钥。
- [ ] 配置校验器能给出中文字段路径和修复提示。
