# RAG-001～RAG-022 修复状态

更新日期：2026-09-03

| Issue | 已落地修复 | 关闭条件 |
|---|---|---|
| RAG-001 | Local 全链路已接通；Production 使用真实 MinerU/Embedding/Zilliz或Milvus/Reranker/LLM、MySQL 检索控制面、真实 Release 水位和 Redis 缓存；`real_acceptance` 由签名证据派生 | 真实业务验收按用户要求暂缓 |
| RAG-002 | 查询驱动 BM25、余弦相似度和 SQLite 持久索引已完成；Local 使用 Jieba search+cnalphanumonly，与真实 Zilliz `chinese` Analyzer 的中英文 Token 输出逐项一致；预置实现仅保留为 Fake | 已完成代码关闭 |
| RAG-003 | 新增业务 Gold Dataset Schema、真实本地检索运行、分桶指标和阈值门禁 | 业务负责人扩充并裁决真实题集后完成真实质量验收 |
| RAG-004 | 独立 ChunkerPort、Token/结构/Embedding 语义策略、Overlap、上下限、稳定 ID、元数据和 Parent-Child 已接入 Runtime；Markdown/HTML/DOCX/PPTX 保留 Heading | 已完成代码关闭，参数调优随真实 Gold Dataset 暂缓 |
| RAG-005 | Zilliz/Milvus 按记录数和字节数批量 upsert，具备稳定主键、批次重试及完整失败 Chunk ID；真实 Zilliz 批写与清理已通过 | 已完成代码关闭 |
| RAG-006 | BM25 与 Embedding+Dense 并发，真实 Milvus 异常已映射为可降级错误并分别记录 Span/告警 | 已完成代码关闭；native hybrid 保留为可选优化 |
| RAG-007 | 新增加权 RRF 与 identifier/keyword/semantic 查询分类，精确查询提高 BM25 权重 | 已完成代码关闭，权重由质量门禁约束 |
| RAG-008 | NFKC/空白/标点归一化、shingle 近重复及单文档/单章节多样性限制 | 已完成代码关闭 |
| RAG-009 | 模型连接改为长生命周期连接池，独立超时、并发限制、429/5xx 重试、退避、Retry-After、熔断和指标；异步端点把同步 SDK 工作移入线程池 | 已完成代码关闭 |
| RAG-010 | 真实 LLM、低温配置、独立 Prompt/Model 版本、严格 JSON/Citation 契约及 Redis 验证后缓存已接入；真实合成公开输入探测通过 | 已完成代码关闭；业务效果随 RAG-003 暂缓 |
| RAG-011 | Search/QA 只捕获类型化的临时 Provider/响应异常；配置、维度、Schema 与未知编程错误不再被静默降级 | 已完成核心路径关闭 |
| RAG-012 | 加入真实恶意 HTML 语料并贯穿 Parser→Chunk→Index→Retrieve→Prompt→Generator；Prompt 将 Evidence 标为不可信，引用与权限继续 fail closed | Mock 安全回归已关闭；真实 LLM 攻击验收只在受保护工作流执行 |
| RAG-013 | `LLM_ALLOW_HTTP` 默认关闭；production 中开关绕过、`http://`、Local Profile 均在 G0 阻止启动 | 已完成代码关闭 |
| RAG-014 | Query 与 document parse/chunk/embedding batch/vector write 均有嵌套 Span，OTLP 依赖已锁定并提供可配置性能脚本 | 大规模真实性能运行按用户要求暂缓 |
| RAG-015 | PR CI 与受保护 Provider Workflow 已配置，Linux Mypy 平台错误及 Node20 Action 警告已修复 | 远端重跑和 branch protection 按用户要求暂缓 |
| RAG-016 | 新增 Python 3.12 精确依赖锁、branch coverage 与 70% fail-under；当前隔离外部适配器后的单元覆盖率实测 71.87% | 已完成代码关闭 |
| RAG-017 | 保留原生运行，并增加 Backend/Worker/Frontend Dockerfile、Compose、DevContainer 与 dockerignore | 当前主机未安装 Docker；需在 CI/部署机完成镜像构建验证 |
| RAG-018 | Application 仅依赖 HybridIndexPort；Local、Zilliz 和自托管 Milvus 使用独立解析后的 URI/字段/维度/Metric 配置 | 已完成代码关闭 |
| RAG-019 | Parser 已拆分为 common、text/PDF、Office、offline、MinerU 与 router；Zilliz 拆分 schema、readiness、lifecycle probe、writer 和 search | 已完成代码关闭 |
| RAG-020 | 前端增加初次上传 UI、API/组件测试及 Playwright；浏览器链路真实执行上传→独立 Worker→复核→发布→检索问答→引用 | 已完成代码关闭 |
| RAG-021 | README 重写为 Windows 与 Linux/macOS Quick Start，明确 Local/Production、真实/Mock、容器和质量流程，移除个人绝对路径 | 已完成代码关闭 |
| RAG-022 | 新增 CONTRIBUTING、Issue/PR 模板、CODEOWNERS、ADR、Changelog 与工作流 | 新治理从后续 Issue/PR/Release 开始积累，历史不能由代码补造 |

普通离线门禁不消费 Provider、不读取生产秘密。历史真实 Provider 产物绑定测试使用
`integration` Marker，只有受保护的 `real-rag-acceptance` Environment 才会执行。

## RAG-023～RAG-041 当前整改

- RAG-023/024：未审核索引统一写成最高密级、空 ACL、STAGED/current=false；审核冻结不可变
  SecurityProjection，release 提供真实 generation、permission revision 和 watermark。
- RAG-025：Production 上传、生命周期、RAG Run、引用、治理和检索状态使用 MySQL，队列/租约/
  缓存使用 Redis；原件仍为本地文件，因此配置门禁强制单实例。
- RAG-026：OIDC Discovery/JWKS、签名、issuer/audience/exp/nbf、算法、时钟偏差、未知 kid
  刷新、tenant/scope/clearance 映射已接入。
- RAG-027：Local 使用 FTS5 与持久 USearch generation 快照；同一 generation 不再逐请求解析
  全部 JSON 向量。
- RAG-028：Zilliz 写入使用稳定 upsert、未知结果主键确认、MySQL index job/batch Saga 账本、
  BUILDING→READY 对账；MySQL release 最后推进以保持失败关闭。
- RAG-029/030：Generator 输出原子 Claims，证据使用 JSON；确定性事实/URL/凭证策略与独立
  Verifier 全部通过后才允许 `verified=true`。
- RAG-031/032：SemanticChunker 已接入；Production 强制内容哈希固定的正式 tokenizer，句子
  边界优先且表格 Chunk 携带表头。
- RAG-033～035：四类模型使用独立连接池和并发门及整体 deadline；中文分类不依赖空格；
  分数校准融合并在 Rerank 后执行近重复删除。
- RAG-036/037：`httpx` 已进入正式依赖；CI 拆为独立安装、静态检查、测试、质量、E2E、容器
  和依赖审计 Job，artifact-bound 历史测试不再隐式依赖本机未跟踪目录。
- RAG-038：真实验收固定为经业务签名的 10 条 Gold、1/5/20 Chunk、三种恶意文件、最多
  60 次调用/20 万输入/2 万输出 Token；性能仅建立低置信度基线，不声明 SLO。
- RAG-039/040：引用签名使用 active/retiring `kid` keyring；Production 无 keyring 拒绝启动；
  verified-answer cache 使用 Redis。
- RAG-041：Actions 固定 commit SHA，并增加依赖审计、CodeQL、Trivy、SBOM、Cosign 与构建
  provenance 工作流。

本机真实 Zilliz schema、批量写入、检索与清理已通过。MySQL/Redis 本机端口当前不可达；真实
Gold、独立 Verifier 与正式 tokenizer 未配置前，受保护工作流必须在首次计费请求前失败关闭。
