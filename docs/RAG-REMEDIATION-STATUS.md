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

## RAG-046～RAG-055 补充整改

- RAG-042：索引 Saga 使用 v3 attempt-aware Job/Batch 账本。`FAILED` 或 manifest 变化会原子
  增加 attempt 并清除旧批次；重复批次必须拥有相同 Chunk manifest 和 checksum。READY 前验证
  连续批号、无重复 Chunk、完整 Chunk 集合、聚合 checksum、Vector/Control 双确认及状态转移
  rowcount；Worker 只在再次读取到 Saga `READY` 后提升版本索引状态。
- RAG-043：答案正文在 Provider Verifier 前确定性拆成原子子句，每个实质子句必须由带证据的
  Claim 完整覆盖；独立 Verifier 同时接收完整 answer 与覆盖清单。最终答案不再直接返回模型
  原始正文，而是仅从全部验证通过的 Claims 程序化重建；覆盖失败不会调用 Provider 或写入缓存。
- RAG-044：低成本真实验收读取并哈希绑定 `config/rag-quality-thresholds.json` 的全部非零阈值，
  全局质量、逐条 Gold 答案 F1、权限/拒答、Prompt Injection 和清理均为最终硬门禁。报告记录
  1/5/20 实际 generation 和每条用例使用的 generation；签名器删除所有可伪造的 Provider、
  revision、dataset 和 generation CLI 参数，只能签署报告中实际受测的 20-Chunk generation。

- RAG-046：Production 聚合由“每租户单行 JSON”迁移为按实体独立行的 v3 表，使用实体级
  revision 做乐观并发；MySQL 连接由有界池复用并在应用关闭时回收。
- RAG-047：Production 发布先持久化不可见的 `SWITCHING` 意图与 Outbox，再执行外部投影；
  投影成功后在同一 MySQL 事务中提交 Lifecycle、Upload 当前版本和 Outbox 状态。任一步失败
  均保持不可 Serving，并允许使用同一幂等键恢复。
- RAG-048/049：Semantic Chunk 保留节点类型、来源 spans、页/幻灯片/工作表边界和正式
  tokenizer，并批量预取边界 Embedding；LLM generation context 明确携带标题、结构路径、
  表头和授权父级上下文。
- RAG-050：Local ANN 使用单调 generation revision 与完整 manifest hash 失效缓存，并增加
  内存 LRU、磁盘保留上限和退休 generation 清理。
- RAG-051：单结果/同分结果使用通道绝对分数校准，不再自动获得 1.0；Embedding 暂时失败时
  Native Hybrid 仍单独执行 BM25。
- RAG-052：Worker 将临时 Provider 错误、永久契约/配置错误和未知错误分流；批次间检查取消，
  取消时幂等清理版本级向量、控制面和本地产物。
- RAG-054：Liveness/Readiness 返回实际验收状态；Readiness 检查 MySQL、Redis Queue、Release、
  索引水位和磁盘，Provider 使用非计费的缓存熔断状态，并提供 acceptance/degraded 状态端点。
- RAG-055：三个基础镜像固定 digest，容器使用非 root、HEALTHCHECK、只读根文件系统、清空
  Linux capabilities；三种镜像均执行 Trivy 和 CycloneDX SBOM，Python 锁文件包含 hashes，
  并补充 LICENSE 与 NOTICE。

## P1 大文件上传内存整改

- Backend 上传路由直接消费 `request.stream()`，单遍完成隔离区写入、SHA-256 和字节计数；
  `Content-Length` 快速拒绝之外仍有流内硬上限，失败或中断会删除 `.uploading` 临时文件。
- 隔离区使用 `UPLOAD_QUARANTINE_MAX_GB` 容量预留，避免并发超卖；
  `UPLOAD_MAX_CONCURRENT_STREAMS` 限制并发流并形成背压。
- Frontend 使用 2 MiB Blob slice 增量 SHA-256，不再整文件 `arrayBuffer()`；Backend 流式硬上限
  是不依赖代理、不可绕过的最终保护边界。

## P1 压缩包资源限制与 Worker 重试风暴整改

- OOXML 校验新增累计解压字节、单条目解压字节、全局条目数和嵌套深度硬上限；嵌套包使用
  有界内存的 spooled 临时文件递归扫描。校验运行在独立子进程，父进程执行墙钟超时终止，
  POSIX 子进程同时设置 CPU rlimit。文件魔数和 UTF-8 检查也改为分块读取。
- Worker 按 `base * 2^(attempt-1) + jitter` 计算有上限延迟，暂时、永久和未知错误使用不同
  重试策略；连续依赖错误触发租约冷却。队列最终失败写入 SQLite/Redis DLQ，手动重试时移出。
  主循环按失败建议延迟休眠且不再在队列状态保存后抛异常；租约排序优先新任务，避免重试挤压。
- Worker 安全日志包含 Job/Document/attempt/异常类型/trace ID/重试和延迟，不包含异常正文。
