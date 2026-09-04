# 企业级 RAG 知识库

这个仓库提供一条可审计的 RAG 链路：上传、解析、Token/结构化分片、Embedding、
BM25 + Dense 检索、查询类型感知融合、Rerank、基于证据生成、引用校验和最终权限复核。

系统有两个明确分离的运行模式：

- `RAG_RUNTIME_PROFILE=local`：本地 SQLite FTS5 与持久 USearch generation 快照会真实计算
  BM25 和 ANN 相似度；
  Embedding、Reranker 与答案生成保持确定性，因此结果始终是 `real_acceptance=false`。
- `RAG_RUNTIME_PROFILE=production`：装配真实 OpenAI-compatible Embedding、Reranker、LLM、
  独立 Verifier 及 Zilliz/Milvus；上传、生命周期、RAG Run、引用和治理状态使用 MySQL，
  且上传/生命周期/治理按实体行存储并使用乐观 revision；队列、租约和缓存使用 Redis。
  MySQL 使用有界连接池。生产模式禁止明文 HTTP 和确定性/Fake Provider。

`real_acceptance=true` 不能由一个布尔配置打开。只有真实评测生成的验收证据文件，其 Provider、
模型、Prompt、索引代际、数据集版本和指标均与当前运行时一致，并通过受保护密钥 HMAC 签名、
提交绑定和有效期校验时，系统才会返回该标记。

## 前置条件

- Python 3.12
- Node.js 22（仅前端）
- 本地模式不需要云服务
- 生产模式需要 MySQL、Redis、Zilliz/Milvus 及已批准的模型 Provider

## 本地快速开始

先复制配置模板：

```powershell
# Windows PowerShell
Copy-Item config/.env.example config/.env
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --require-hashes -r requirements.lock
python -m pip install -e . --no-deps
python scripts/check_env.py --gate G0
```

```bash
# Linux / macOS
cp config/.env.example config/.env
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --require-hashes -r requirements.lock
python -m pip install -e . --no-deps
python scripts/check_env.py --gate G0
```

分别启动三个进程：

```text
python run_backend.py
python run_worker.py
cd frontend && npm ci && npm run dev
```

打开 `http://127.0.0.1:5173`。本地数据保存在 `data/storage`，已被 Git 忽略。

## 生产 RAG 模式

生产环境至少需要设置：

```text
APP_ENV=production
APP_DEBUG=false
APP_REVISION=<40 位部署 Commit SHA>
RAG_RUNTIME_PROFILE=production
DEPLOYMENT_TOPOLOGY=single_instance
REAL_PROVIDER_CALLS_ENABLED=true
EXTERNAL_LIFECYCLE_MUTATIONS_ENABLED=true
RAG_ACCEPTANCE_SIGNING_KEY=<受保护 CI Secret>
REFERENCE_ACTIVE_KID=<当前签名 key id>
REFERENCE_SIGNING_KEYRING=<受保护 JSON keyring>
AUTH_MODE=oidc
OIDC_TENANT_ID=<Token tenant_id 必须匹配的部署租户>
OIDC_DEFAULT_SPACE_ID=<默认知识空间>
RETRIEVAL_ACTIVE_GENERATION_ID=<已对账的不可变代际>
VECTOR_BACKEND=zilliz                 # 或 milvus
LLM_ALLOW_HTTP=false
VERIFIER_BASE_URL=<独立核验模型地址>
VERIFIER_API_KEY=<受保护 Secret>
VERIFIER_MODEL=<不得与 LLM_MODEL 相同>
TOKENIZER_ARTIFACT_PATH=<固定 tokenizer.json>
TOKENIZER_ARTIFACT_SHA256=<artifact SHA-256>
TOKENIZER_ID=<正式 tokenizer revision>
```

同时配置模型、向量库、身份、数据区域和密钥。`production + http://` 会在 G0 配置门禁中直接
失败。Production 启动会执行完整 G4 配置门禁，并要求 OIDC Discovery/JWKS、MySQL 检索控制面、
Redis 队列/缓存、真实 MinerU OCR Parser 和外部生命周期写入均可配置。由于原始文件继续保存
在本地磁盘，Production 强制 `single_instance`，不得宣称多实例或高可用。密钥只能放在
`config/.env`、部署 Secret Store 或进程环境中，禁止写入日志和仓库。

Production 发布使用两阶段 fail-closed 协议：先持久化不可见的 `SWITCHING` 意图和 Outbox，
再更新外部投影；只有投影成功后，Lifecycle、当前版本和 Outbox 才在一个 MySQL 事务中提交。
失败或进程中断时 Release 不会被视为可服务，使用同一幂等键可以安全恢复。

向量后端：

- `zilliz` 使用 `ZILLIZ_CLOUD_*`；
- `milvus` 使用通用的 `VECTOR_URI/TOKEN/DATABASE/COLLECTION`；
- `local` 使用 SQLite 持久 BM25 + Dense，仅供开发和离线验收。

Embedding、Reranker、Generator 和 Verifier 分别使用独立连接池、并发门、熔断和整体 deadline。
生成 Prompt 以 JSON 传入不可信 Evidence，不使用可由正文闭合的 XML。答案必须输出原子 Claim，
先通过数字/日期/URL/凭证请求规则，再通过独立 Verifier 的证据蕴含检查。

首次部署先执行显式批准的基础设施操作，再发布检索 Release：

```text
python scripts/provision_mysql_g2.py --approval MYSQL_DATABASE_CREATE_AND_MIGRATE_APPROVED
python scripts/provision_zilliz_g2.py --approval ZILLIZ_COLLECTION_CREATE_APPROVED
python scripts/set_retrieval_release.py --approval RETRIEVAL_RELEASE_UPDATE_APPROVED \
  --tenant-id <tenant> --space-id <space> --generation-id <generation> \
  --permission-revision <revision> --security-watermark <watermark>
```

SQLite 历史状态迁移前必须先生成备份：

```text
python scripts/migrate_sqlite_to_mysql.py \
  --approval SQLITE_TO_MYSQL_MIGRATION_APPROVED \
  --sqlite data/storage/control.sqlite3 \
  --backup data/storage/backups/pre-mysql.sqlite3
```

生产 Worker 将扫描 PDF/图片和旧 Office 文件送入真实 MinerU，验证结果 ZIP 后转换为 Canonical
Document；Markdown、HTML、DOCX 和 PPTX 的标题结构则由本地真实解析器保留。

## 分片与检索

`CHUNK_STRATEGY` 支持 `token`、`structure` 和 `semantic`。Production 使用内容哈希固定的正式
tokenizer artifact；Semantic 路径同样保留节点类型、来源 spans 和 tokenizer，并批量计算边界
Embedding。默认结构化分片保留标题/章节路径，表格行重复携带表头，
搜索使用小 Chunk，回答可附带授权后的 Parent Chunk。大小、Overlap、上下限和 Parent 上限均在
`config/.env` 配置。

检索会并发执行 BM25 与 Embedding/Dense 路径，使用绝对分数与排名稳定项融合；单结果不会
自动成为满置信度，Embedding 暂时失败时仍保留 BM25。中文
长查询默认按语义查询处理，编号/错误码走 identifier。权限过滤后先 Rerank，再执行近重复及
单文档、单章节数量限制。

## 质量与测试

```text
python scripts/check_rag_quality.py
python scripts/run_quality.py
cd frontend && npm run check
cd frontend && npx playwright install chromium && npm run test:e2e
```

质量门禁覆盖 Ruff、Mypy、分支覆盖率、后端测试、前端组件/API/E2E、安全扫描和冻结 Gold
Dataset 的 Recall@K、Precision@K、Hit Rate、MRR、nDCG、答案 F1、引用精确率/召回率与
No-answer Accuracy。阈值位于 `config/rag-quality-thresholds.json`。

普通 PR 只运行离线测试；真实 Provider 调用位于受保护、手动触发的 GitHub Environment，避免
PR 意外产生费用或接触生产密钥。

低成本真实验收固定为 10 条经业务签名的 Gold、1/5/20 Chunk 和三份恶意 PDF/DOCX/OCR
Fixture。所有模型 Provider 合计最多 60 次调用、输入 200,000 Token、输出 20,000 Token，
验收执行器禁止自动重试。性能只记录低置信度基线并固定 `slo_claimed=false`：

```text
python scripts/validate_real_gold.py --dataset <approved-gold.yaml>
python scripts/run_external_lifecycle_drill.py --approved
python scripts/run_low_cost_real_acceptance.py --approved --gold <approved-gold.yaml>
```

## 容器运行

保留原生开发方式，同时提供 Backend、Worker、Frontend 镜像：

```text
docker compose up --build
```

Compose 使用同一个持久卷共享本地存储；Zilliz/Milvus 和模型 Provider 仍是外部服务。三个
镜像均固定基础镜像 digest、使用非 root 用户和 HEALTHCHECK；Compose 使用只读根文件系统、
移除 Linux capabilities，并只开放 Backend 8000 与 Frontend 8080（宿主仍映射为 5173）。

## 可观测性与安全边界

搜索和问答记录 `rag.ask`、evidence build、BM25、Dense、Embedding、fusion、rerank、LLM 与
citation verify 的嵌套 Span。默认在诊断接口中汇总；安装 `.[observability]` 后可由部署层桥接
OpenTelemetry SDK/OTLP。依赖已经进入锁文件，生产镜像无需临时安装。

`/health/live` 只表示进程存活；`/health/ready` 检查控制面、队列、Release、索引水位、磁盘和
缓存的 Provider 熔断状态且不会产生计费调用。`/status/acceptance` 与 `/status/degraded` 分别
表达签名验收绑定和当前降级原因。

权限过滤在检索与生成前执行，生成后再次复核。索引先以最高密级、空 ACL、非 Serving 状态
写入；Saga 对每次 attempt 保存完整 Chunk manifest，失败重试会清空旧批次，只有连续批次、
精确 Chunk 集合、checksum 和 Vector/Control 双确认全部对账并再次读到 `READY` 后才可发布。删除、撤权或索引代际变化
会使答案缓存失效。外部真实调用仍需部署负责人显式开启。

贡献流程、架构决策和发布记录分别见 `CONTRIBUTING.md`、`docs/adr` 与 `CHANGELOG.md`。
