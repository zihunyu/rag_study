# RAG-001～RAG-022 修复状态

更新日期：2026-09-03

| Issue | 已落地修复 | 关闭条件 |
|---|---|---|
| RAG-001 | Local 全链路已接通；Production Profile 禁止 Fake，并装配真实模型与向量后端；`real_acceptance` 改为证据哈希派生 | 受保护环境完成真实 Provider、真实数据、权限/更新/删除 E2E 后生成验收证据 |
| RAG-002 | 新增查询驱动的 BM25、中文/Latin analyzer、余弦相似度与 SQLite 持久本地索引；预置结果实现明确命名为 Fake | 已完成代码关闭 |
| RAG-003 | 新增业务 Gold Dataset Schema、真实本地检索运行、分桶指标和阈值门禁 | 业务负责人扩充并裁决真实题集后完成真实质量验收 |
| RAG-004 | 新增独立 ChunkerPort、Token/结构/语义策略、Overlap、上下限、稳定 ID、元数据和 Parent-Child | 已完成代码关闭，默认参数继续由 Gold Dataset 调优 |
| RAG-005 | Zilliz/Milvus 写入同时按记录数和字节数分批，支持稳定主键、批次重试、失败上下文；合成生命周期也改为批写 | 已完成代码关闭 |
| RAG-006 | BM25 与 Embedding+Dense 两路在线程池并发，分别记录 Span 与降级码 | 已完成代码关闭；供应商支持时可继续评估 native hybrid |
| RAG-007 | 新增加权 RRF 与 identifier/keyword/semantic 查询分类，精确查询提高 BM25 权重 | 已完成代码关闭，权重由质量门禁约束 |
| RAG-008 | 新增 NFKC/空白/标点归一化、shingle 近重复过滤和单文档证据数量限制 | 已完成代码关闭 |
| RAG-009 | 模型连接改为长生命周期连接池，独立超时、并发限制、429/5xx 重试、退避、Retry-After、熔断和指标；异步端点把同步 SDK 工作移入线程池 | 已完成代码关闭 |
| RAG-010 | 新增真实 OpenAI-compatible LLM、低温配置、Prompt 版本、严格 JSON/Citation 契约，以及验证后缓存和修订/证据感知失效 | 已完成代码关闭；真实答案效果由 RAG-001/003 验收 |
| RAG-011 | Search/QA 只捕获类型化的临时 Provider/响应异常；配置、维度、Schema 与未知编程错误不再被静默降级 | 已完成核心路径关闭 |
| RAG-012 | 加入真实恶意 HTML 语料并贯穿 Parser→Chunk→Index→Retrieve→Prompt→Generator；Prompt 将 Evidence 标为不可信，引用与权限继续 fail closed | Mock 安全回归已关闭；真实 LLM 攻击验收只在受保护工作流执行 |
| RAG-013 | `LLM_ALLOW_HTTP` 默认关闭；production 中开关绕过、`http://`、Local Profile 均在 G0 阻止启动 | 已完成代码关闭 |
| RAG-014 | 新增完整嵌套 Span、OTLP 可选桥接、诊断聚合及可配置文档规模/并发性能脚本 | 本地能力已完成；10K/100K/1M 与真实依赖 P95/P99 基线需在目标环境执行 |
| RAG-015 | 新增 PR CI 与受保护真实 Provider Workflow，离线和计费测试分离 | 仓库管理员启用 branch protection 后完成治理关闭 |
| RAG-016 | 新增 Python 3.12 精确依赖锁、branch coverage 与 70% fail-under；当前实测 79.99% | 已完成代码关闭 |
| RAG-017 | 保留原生运行，并增加 Backend/Worker/Frontend Dockerfile、Compose、DevContainer 与 dockerignore | 当前主机未安装 Docker；需在 CI/部署机完成镜像构建验证 |
| RAG-018 | Application 继续只依赖 HybridIndexPort；新增 local/zilliz/milvus 后端与通用 Milvus endpoint 配置/契约测试 | 已完成代码关闭 |
| RAG-019 | Chunking、持久本地索引、向量批写/投影、Tracing、Acceptance 与质量评测均已拆成独立模块；Zilliz 连接与写入职责分离 | Parser 的逐格式进一步拆包可作为后续纯重构，不再阻塞功能 |
| RAG-020 | 前端增加 API 边界、CRLF/分片 SSE、Vue 组件和 Playwright 浏览器 E2E；修复 CRLF SSE 丢帧 | 已完成代码关闭 |
| RAG-021 | README 重写为 Windows 与 Linux/macOS Quick Start，明确 Local/Production、真实/Mock、容器和质量流程，移除个人绝对路径 | 已完成代码关闭 |
| RAG-022 | 新增 CONTRIBUTING、Issue/PR 模板、CODEOWNERS、ADR、Changelog 与工作流 | 新治理从后续 Issue/PR/Release 开始积累，历史不能由代码补造 |

普通离线门禁不消费 Provider、不读取生产秘密。历史真实 Provider 产物绑定测试使用
`integration` Marker，只有受保护的 `real-rag-acceptance` Environment 才会执行。
