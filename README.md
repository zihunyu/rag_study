# 企业级 RAG 知识库

这个仓库提供一条可审计的 RAG 链路：上传、解析、Token/结构化分片、Embedding、
BM25 + Dense 检索、查询类型感知融合、Rerank、基于证据生成、引用校验和最终权限复核。

系统有两个明确分离的运行模式：

- `RAG_RUNTIME_PROFILE=local`：本地 SQLite 持久索引会真实计算 BM25 和余弦相似度；
  Embedding、Reranker 与答案生成保持确定性，因此结果始终是 `real_acceptance=false`。
- `RAG_RUNTIME_PROFILE=production`：装配真实 OpenAI-compatible Embedding/Reranker/LLM
  及 Zilliz 或 Milvus。生产模式禁止明文 HTTP，也禁止任何 `Deterministic*`/`Fake*` 组件。

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
python -m pip install -r requirements.lock
python -m pip install -e . --no-deps
python scripts/check_env.py --gate G0
```

```bash
# Linux / macOS
cp config/.env.example config/.env
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.lock
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
REAL_PROVIDER_CALLS_ENABLED=true
EXTERNAL_LIFECYCLE_MUTATIONS_ENABLED=true
RAG_ACCEPTANCE_SIGNING_KEY=<受保护 CI Secret>
AUTH_MODE=oidc
OIDC_TENANT_ID=<Token tenant_id 必须匹配的部署租户>
OIDC_DEFAULT_SPACE_ID=<默认知识空间>
RETRIEVAL_ACTIVE_GENERATION_ID=<已对账的不可变代际>
VECTOR_BACKEND=zilliz                 # 或 milvus
LLM_ALLOW_HTTP=false
```

同时配置模型、向量库、身份、数据区域和密钥。`production + http://` 会在 G0 配置门禁中直接
失败。Production 启动会执行完整 G4 配置门禁，并要求 OIDC Discovery/JWKS、MySQL 检索控制面、
Redis 验证答案缓存、真实 MinerU OCR Parser 和外部生命周期写入均可配置。密钥只能放在
`config/.env`、部署 Secret Store 或进程环境中，禁止写入日志和仓库。

向量后端：

- `zilliz` 使用 `ZILLIZ_CLOUD_*`；
- `milvus` 使用通用的 `VECTOR_URI/TOKEN/DATABASE/COLLECTION`；
- `local` 使用 SQLite 持久 BM25 + Dense，仅供开发和离线验收。

模型 HTTP 连接在应用生命周期内复用，具有独立 connect/read/write/pool timeout、并发上限、
429/502/503/504 重试、指数退避、`Retry-After` 支持和熔断。生成 Prompt 把检索内容明确标为
不可信数据，最终答案还必须通过引用和权限二次校验。

首次部署先执行显式批准的基础设施操作，再发布检索 Release：

```text
python scripts/provision_mysql_g2.py --approval MYSQL_DATABASE_CREATE_AND_MIGRATE_APPROVED
python scripts/provision_zilliz_g2.py --approval ZILLIZ_COLLECTION_CREATE_APPROVED
python scripts/set_retrieval_release.py --approval RETRIEVAL_RELEASE_UPDATE_APPROVED \
  --tenant-id <tenant> --space-id <space> --generation-id <generation> \
  --permission-revision <revision> --security-watermark <watermark>
```

生产 Worker 将扫描 PDF/图片和旧 Office 文件送入真实 MinerU，验证结果 ZIP 后转换为 Canonical
Document；Markdown、HTML、DOCX 和 PPTX 的标题结构则由本地真实解析器保留。

## 分片与检索

`CHUNK_STRATEGY` 支持 `token`、`structure` 和 `semantic`。默认结构化分片保留标题/章节路径，
搜索使用小 Chunk，回答可附带授权后的 Parent Chunk。大小、Overlap、上下限和 Parent 上限均在
`config/.env` 配置。

检索会并发执行 BM25 与 Embedding/Dense 路径，并按查询类型调整权重：型号、错误码等精确查询
提高 BM25 权重，自然语言查询保持默认 Hybrid。送入模型前还会执行 Unicode/标点归一化、
近重复过滤及单文档、单章节证据数量限制。

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

## 容器运行

保留原生开发方式，同时提供 Backend、Worker、Frontend 镜像：

```text
docker compose up --build
```

Compose 使用同一个持久卷共享本地存储；Zilliz/Milvus 和模型 Provider 仍是外部服务。

## 可观测性与安全边界

搜索和问答记录 `rag.ask`、evidence build、BM25、Dense、Embedding、fusion、rerank、LLM 与
citation verify 的嵌套 Span。默认在诊断接口中汇总；安装 `.[observability]` 后可由部署层桥接
OpenTelemetry SDK/OTLP。依赖已经进入锁文件，生产镜像无需临时安装。

权限过滤在检索与生成前执行，生成后再次复核。删除、撤权或索引代际变化会自然使答案缓存
失效。外部真实调用仍需部署负责人显式开启，所有状态和异常只能暴露稳定错误码。

贡献流程、架构决策和发布记录分别见 `CONTRIBUTING.md`、`docs/adr` 与 `CHANGELOG.md`。
