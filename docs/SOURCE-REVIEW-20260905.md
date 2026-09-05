# 当前源码审查与整改方案

审查日期：2026-09-05。基准提交：`5b80865`（`feat: expand knowledge base management workflow`）。审查开始时工作区干净。本报告新增后，业务源码未修改。

本次发现 23 项问题及不足：15 项 P1、8 项 P2。P1 表示应优先修复的权限、数据一致性或核心流程问题；P2 表示可靠性、可维护性与产品能力不足。优先级不等于已发生事故；下文区分了实际隔离复现与源码推导的触发条件。

审查覆盖知识库、上传、版本、分块、发布、撤权、问答、认证、MySQL/Redis/Zilliz 适配器、Worker、前端状态及测试门禁。没有调用真实模型、MinerU 或 Zilliz，没有改动现有业务数据库、知识库、服务配置和云端资源。复现使用临时 SQLite、合成身份或适配器测试替身；临时目录由测试退出时自动清理。未执行远端 CI、大规模压测及正式 Gold 验收。

## 复现结果

| 隔离检查 | 实际结果 | 关联问题 |
|---|---|---|
| 普通 reader 查看尚未发布的文档列表 | HTTP 200，返回文件元数据 | SR-01 |
| reader 检索仅 admin 有权查看、密级 3 的文档 | 0 条命中，符合检索权限策略 | SR-01 对照组 |
| 同一 reader 直接读取上述版本的 chunks | HTTP 200，返回 2 条，包括受限正文 | SR-01 |
| 自建知识库的文档创建新版本上传会话 | 返回的 space_id 是默认知识库 | SR-02 |
| Verifier 执行期间将最终权限检查器切换为拒绝 | 最终仍为 answered、verified=true | SR-03 |
| 生命周期内存旧快照与模拟数据库较新状态合并 | 生成 DELETE 新文档 B、将已撤权 A 改回 ACTIVE 的 SQL | SR-04 |
| MySQL 发布检查输入 BLOCKED_REAL_VALIDATION 及过期审核 revision | ready=true | SR-05 |
| 旧候选仍为 STAGED 时申请 rollback | PUBLICATION_PROJECTION_NOT_STAGED | SR-06 |
| 审核 A 全租户可见 → 审核 B 受限 → 使用原幂等键重放 A | 最新审核记录为 RESTRICTED，实际投影却变回 TENANT | SR-07 |
| OIDC 身份验证器下发送合法浏览器 OPTIONS 预检 | HTTP 401，没有 CORS 放行头 | SR-08 |
| 让普通访问日志仓储写入抛出异常后访问 /health/live | HTTP 500 | SR-13 |
| 只改变 verifier_revision，比较问答缓存键 | 缓存键完全相同 | SR-22 |

另外运行了当前四个相关测试模块：`test_knowledge_base_api.py`、`test_mysql_normalized_state.py`、`test_trusted_qa.py`、`test_worker_resilience.py`，结果为 **29 passed**。上述新发现没有被这些现有测试充分覆盖，测试通过不能证明这些边界已安全。

## P1：优先修复

### SR-01：文档列表、版本和分块接口绕过细粒度读取权限

代码：[support.py:130](E:/Data/codex/20260831rag/backend/src/ragkb/api/support.py:130)、[documents.py:122](E:/Data/codex/20260831rag/backend/src/ragkb/api/routers/documents.py:122)、[spaces.py:90](E:/Data/codex/20260831rag/backend/src/ragkb/api/routers/spaces.py:90)。

`ensure_document_readable()` 对 reader 只检查文档是否 ACTIVE/visible，不检查 ACL、密级、有效期和请求的具体版本；列表接口甚至不逐文档检查。维护者角色直接放行，也没有独立的管理权限范围。检索拒绝的正文可以通过 chunks 接口读出；只要某个版本已发布，其他未发布版本也缺少对应的版本级读取门禁。

解决方案：把检索与内容读取接到同一授权策略，传入 tenant、space、user/group/scope、clearance、version、有效期和安全 revision；普通读者仅返回有权读取的当前已发布版本。管理草稿必须使用明确的管理权限。列表在数据库查询阶段过滤，正文读取再次复核，未授权统一返回 404。

验收：同租户不同 ACL/密级、草稿、历史版本、过期、撤权后的列表/详情/版本/chunks/source 全部跑矩阵测试，确认不泄漏正文及敏感文件名。

### SR-02：非默认知识库的文档，新版本被写入默认知识库

代码：[uploads.py:120](E:/Data/codex/20260831rag/backend/src/ragkb/api/routers/uploads.py:120)。

`create_version_upload_session()` 固定传入 `space_id=runtime.space_id`。Worker 后续从该 session 派生的 job payload 读取 space_id，所以自建知识库 A 的新版本会被索引到默认知识库。原知识库检索不到新版本，默认知识库可能出现不属于它的内容。已通过接口复现错误归属。

解决方案：在文档控制面保存不可变的 tenant_id/space_id；新版本只从原文档读取归属，仓储强制验证会话、文档、版本、索引作业的归属一致。禁止以客户端值或默认值代替。

验收：在两个知识库分别上传 v1、v2，发布、回滚后检查文件列表及检索命中始终留在原库。

### SR-03：最终权限复核发生在 Verifier 之前，存在撤权窗口

代码：[qa.py:424](E:/Data/codex/20260831rag/backend/src/ragkb/application/qa.py:424)、[rag_stubs.py:123](E:/Data/codex/20260831rag/backend/src/ragkb/adapters/rag_stubs.py:123)。

所谓 final permission check 在独立 Verifier 调用之前执行。Verifier 可能耗时数秒到数十秒，之后直接签引用、写缓存、返回答案。隔离复现中，在 Verifier 内使权限变为拒绝，最终仍得到 verified=true。另外，Production 使用的 `LifecycleAwareFinalPermission` 丢弃 scope_tokens，只检查内存生命周期和 revision，不重新读取实际 ACL/clearance。

解决方案：在生成前及 Verifier 完成后读取最新的持久化安全投影，逐条校验 ACL、密级、有效期、当前版本和 revision。签发、缓存及返回必须绑定通过复核的安全 revision；若在处理过程中发生撤权或版本变化，拒绝输出并失效引用。引用访问也应用同一策略。

验收：在检索、Generator、Verifier、签发前分别注入撤权/换版，均不得返回受保护正文；用相同 revision 但不同主体权限验证不会仅依赖 authorized 快照。

### SR-04：MySQL 生命周期保存可用旧内存状态覆盖新数据库状态

代码：[mysql_lifecycle.py:317](E:/Data/codex/20260831rag/backend/src/ragkb/adapters/mysql_lifecycle.py:317)、[mysql_entity_store.py:81](E:/Data/codex/20260831rag/backend/src/ragkb/adapters/mysql_entity_store.py:81)。

`persist_state()` 将刚从数据库加载的最新数据作为 before，却将可能过期的整个内存状态作为 after。`sync()` 会删除 before 有、after 没有的实体，并使用最新读到的 entity_revision 更新旧内容。因此所谓乐观锁不能识别“操作开始前就已过期”的内存快照。隔离模拟实际生成了删除新实体、复活已撤权旧实体的 SQL。触发条件是内存状态过期或并发状态修改，并非已经证明当前业务库发生损坏。

解决方案：在一次事务内加载目标聚合、执行命令并按最初读取的 revision 更新；只保存明确修改的实体，不把全量快照差集解释成删除。删除必须是显式命令。审核事件使用独立追加记录；必要时对目标文档行加锁，冲突返回 409/412。共享内存刷新采用原子替换，避免在并发读取时先 clear 再填充。

验收：两个独立仓储实例交错执行注册、发布、撤权、删除，验证不会丢实体、丢审核事件或恢复已撤销权限。

### SR-05：Production 发布门禁弱于 Local，可能发布占位解析内容

代码：[mysql_upload.py:696](E:/Data/codex/20260831rag/backend/src/ragkb/adapters/mysql_upload.py:696)、[publication_readiness.py:58](E:/Data/codex/20260831rag/backend/src/ragkb/adapters/publication_readiness.py:58)、[production.py:251](E:/Data/codex/20260831rag/backend/src/ragkb/runtime_profiles/production.py:251)。

MySQL 发布检查只检查质量报告存在，没有像 SQLite 实现一样拒绝 BLOCKED_REAL_VALIDATION，也没核对审核绑定的 quality revision。构造被阻止的质量报告和过期审核，仍返回 ready=true。Production parser 没有覆盖 audio，仍继承 OfflineASRStubParser；这与宽松发布门禁组合后，可能把 ASR 占位文本当作正式知识发布。

解决方案：抽取所有 profile 共用的纯发布策略，要求真实解析能力、合格质量、审核/质量/安全投影 revision 一致、目标文档归属正确及索引对账通过。Production 未配置 ASR 时，在能力接口和上传前明确标记不支持；需要音频能力时接入真实 ASR 适配器，不能让 stub 获得生产发布资格。

验收：使用相同输入同时测试 Local/MySQL readiness；stub、质量阻止、审核过期、错误文档归属均必须失败关闭。

### SR-06：MySQL 发布没有推进候选状态，正常版本回滚会被卡住

代码：[mysql_lifecycle.py:215](E:/Data/codex/20260831rag/backend/src/ragkb/adapters/mysql_lifecycle.py:215)、[mysql_upload.py:719](E:/Data/codex/20260831rag/backend/src/ragkb/adapters/mysql_upload.py:719)。

索引完成将 candidate 标为 STAGED。发布事务更新文档当前版本与 version 的 SERVING/SUPERSEDED，但没有把旧 candidate 改为 RETIRED；源码中 MySQL candidate 没有 RETIRED 写入路径。rollback readiness 却要求目标为 RETIRED，因此正常的 v1 → v2 → v1 回滚流程不闭合。

解决方案：在发布控制面事务里同步推进当前版本、版本状态、目标 candidate 和旧 candidate；回滚使用同一状态机。对历史已发布数据提供候选状态对账和修复迁移。

验收：MySQL 隔离库完整执行发布 v1、发布 v2、回滚 v1，并检查候选、release、向量与页面状态一致。

### SR-07：旧审核请求重放会覆盖最新安全投影

代码：[documents.py:190](E:/Data/codex/20260831rag/backend/src/ragkb/api/routers/documents.py:190)、[documents.py:214](E:/Data/codex/20260831rag/backend/src/ragkb/api/routers/documents.py:214)。

仓储检测幂等重放后返回旧结果，但路由仍用本次请求重新计算的 security 对投影执行写入。实测：审核 A 为 TENANT，审核 B 为 RESTRICTED，再重放 A，数据库最新审核仍是 B，实际投影却恢复为 TENANT。审核记录和外部投影也是先后提交，存在部分失败后的不一致。

解决方案：审核、安全 revision、投影更新意图在一个事务里提交；Outbox 事件绑定 review_id、目标 revision 和投影 checksum。消费端拒绝旧 revision，幂等重放只返回原结果，不重复产生副作用。已发布内容变更权限应进入专门的安全状态迁移。

验收：A→B→重放 A、重复 B、MySQL 成功而 Zilliz 失败、投影成功但响应丢失等场景，最终审核与投影必须一致。

### SR-08：OIDC 模式下，跨域直连被认证中间件拦截

代码：[application.py:61](E:/Data/codex/20260831rag/backend/src/ragkb/api/application.py:61)、[application.py:70](E:/Data/codex/20260831rag/backend/src/ragkb/api/application.py:70)。

认证中间件处于 CORS 处理之前，也要求 OPTIONS 带 Authorization。浏览器预检不携带实际 Bearer token，因此请求在进入真正业务接口之前就返回 401。隔离预检已复现。CORS 配置也没有 expose_headers，跨域客户端无法读取 ETag/X-Request-ID。Local 单用户模式及 Vite 同源代理会掩盖问题。

解决方案：让 CORS 正确处理预检、确保响应错误也带适当 CORS 头；实际业务请求仍正常认证。显式 expose ETag、X-Request-ID 等前端确实需要的头。

验收：以不同前后端端口、OIDC 测试签名身份执行真实浏览器上传、版本更新、问答及未授权错误处理，不依赖 Nginx。

### SR-09：权限更新接口没有更新实际的访问主体

代码：[models.py:256](E:/Data/codex/20260831rag/backend/src/ragkb/api/models.py:256)、[lifecycle.py:109](E:/Data/codex/20260831rag/backend/src/ragkb/api/routers/lifecycle.py:109)、[application/lifecycle.py:486](E:/Data/codex/20260831rag/backend/src/ragkb/application/lifecycle.py:486)。

PermissionUpdateRequest 只有 revision、水位和 projection_ok，没有新的 ACL、visibility、密级或有效期。点击“更新权限”实际主要推进数字版本，旧 ACL 保持不变；observed_watermark 和 projection_ok 还来自客户端，不能证明外部存储已完成变更。

解决方案：请求提交期望的权限策略及 If-Match；服务端生成 revision 和 Outbox，持久化并同步真正的授权字段。水位、checksum 和成功状态只能由服务端核验 MySQL/Zilliz 后推进，不接受客户端自证。

验收：将用户 A 替换为 B，确认 A 的检索、分块和已有引用全部失效，B 获得访问；任一投影失败时不恢复 Serving。

### SR-10：MySQL 检索控制面没有按 generation 隔离

代码：[mysql_migrations.py:114](E:/Data/codex/20260831rag/backend/src/ragkb/infrastructure/mysql_migrations.py:114)、[mysql_retrieval.py:90](E:/Data/codex/20260831rag/backend/src/ragkb/adapters/mysql_retrieval.py:90)。

retrieval_chunk_projections 以 chunk_id 单独作主键，没有 generation_id。authorize_chunks 也不按 SearchContext.active_generation_id 过滤。对相同文档版本构建新 generation 时，相同 Chunk ID 的投影会覆盖旧 generation 的正文/权限/状态；新索引仍处于构建阶段就可能影响当前 Serving，release 快照不再真正不可变。

解决方案：使用 tenant+space+generation+chunk 的复合身份；父块、权限与正文投影都按 generation 保存。构建只写目标 generation，对账后原子切换 release；旧 generation 等在途请求完成后再退休。相关更新/清理 API 也要显式指定 generation。

验收：旧 generation 持续问答时构建新 generation，并分别在写入、对账、切换处中断，旧 release 的数据与权限必须稳定。

### SR-11：向量写入超时后的确认只验证主键存在

代码：[vector_indexing.py:165](E:/Data/codex/20260831rag/backend/src/ragkb/adapters/vector_indexing.py:165)、[vector_indexing.py:188](E:/Data/codex/20260831rag/backend/src/ragkb/adapters/vector_indexing.py:188)。

upsert 结果未知时，只查询 zilliz_pk 集合；集合存在就当作本次写入成功。主键没有绑定本次 attempt、模型、retrieval_text 或安全投影。若同一主键已有旧记录，而本次更新未落地，旧记录存在不能证明此次 payload 已生效。当前 Saga 的 Chunk 文本 checksum 也不足以绑定全部这些字段。

解决方案：写入批次/attempt 标识和完整投影 digest；未知结果采用满足安全一致性的查询，逐条核对 identity、digest、模型与安全 revision。匹配才确认，无法确认则保持 UNKNOWN/BUILDING；重发必须明确遵守幂等和预算约束。

验收：预置旧同主键不同向量/检索文本/ACL 记录，让新 upsert 超时且未生效，禁止凭主键存在推进 READY。

### SR-12：多个 async 路由直接执行阻塞数据库、文件及远程调用

代码：[uploads.py:182](E:/Data/codex/20260831rag/backend/src/ragkb/api/routers/uploads.py:182)、[lifecycle.py:45](E:/Data/codex/20260831rag/backend/src/ragkb/api/routers/lifecycle.py:45)、[documents.py:161](E:/Data/codex/20260831rag/backend/src/ragkb/api/routers/documents.py:161)。

问答已移入线程池，但上传 complete、审核、发布、列表等仍在 async 函数内直接执行同步调用。上传 complete 会校验文件/等待压缩包校验子进程，发布会等待 Zilliz/MySQL。单个慢调用可阻塞同一事件循环上的健康检查、其他上传和任务轮询。

解决方案：按完整业务调用边界使用受限线程池，或改为异步驱动；耗时解析留在 Worker，发布/审核可返回持久化作业并查询状态。线程并发引入前先修复 SR-04 的共享状态和事务边界。

验收：注入 5 秒的校验或存储延迟，同时访问 live/任务状态，验证请求不会被串行阻塞；线程池队列必须有界。

### SR-13：普通访问日志写入失败会把业务成功和存活检查变成 500

代码：[application.py:87](E:/Data/codex/20260831rag/backend/src/ragkb/api/application.py:87)、[observability.py:18](E:/Data/codex/20260831rag/backend/src/ragkb/application/observability.py:18)。

中间件在业务响应生成后同步写 governance event，异常不隔离。模拟日志仓储异常，/health/live 实际返回 500。业务操作也可能已经完成，但客户端收到失败并重试。Production 中健康探测本身还会持续写 MySQL 事件。

解决方案：普通访问日志使用有界异步导出/本地日志，失败计入独立指标而不改写业务响应；live 不依赖日志数据库。必须持久化的业务审计应放进业务事务或 Outbox，和可丢弃的访问遥测分开。

验收：日志服务不可用时 live 和已完成的业务保持正确响应；关键审核/权限事件仍不能被静默丢弃。

### SR-14：Worker 长耗时阶段缺少持续租约心跳和失租保护

代码：[worker.py:193](E:/Data/codex/20260831rag/backend/src/ragkb/application/worker.py:193)、[worker.py:200](E:/Data/codex/20260831rag/backend/src/ragkb/application/worker.py:200)、[redis_queue.py:237](E:/Data/codex/20260831rag/backend/src/ragkb/adapters/redis_queue.py:237)。

读取版本/路径还在任务处理 try 块之外；解析及切块完成后才第一次续租。OCR、大文档或单次 Provider 调用超过租约时，另一个 Worker 可回收同一任务，而旧 Worker 没有写入 fencing token。出错后可能等租约到期才能恢复，重叠执行也会额外消耗模型费用。

解决方案：把全部任务初始化纳入受控失败路径；后台按 lease/3 续租，租约丢失立即停止后续副作用。每次租约生成单调 fencing token，索引/账本/完成状态拒绝旧 token。单次解析采用可终止子进程或可取消作业。

验收：用可控时钟模拟超租约解析、读取版本失败和第二 Worker 接管，确保旧 Worker 不能继续确认或覆盖新结果。

### SR-15：Redis 入队的幂等记录和任务记录并非原子写入

代码：[redis_queue.py:148](E:/Data/codex/20260831rag/backend/src/ragkb/adapters/redis_queue.py:148)。

enqueue 先 hset 幂等键，再另一次 hset 保存任务。两条命令之间发生连接中断或进程退出，会留下指向不存在任务的幂等记录；同键重试走 _required() 后报错，任务无法自动入队。分布式锁只能协调并发，不能使两次写入具备崩溃原子性。

解决方案：使用 Lua 或事务原子提交幂等映射、任务、就绪队列；幂等命中必须核对目标记录存在。完善异常中断对账，并将上传控制面与队列提交通过 Outbox 衔接。

验收：在每个入队持久化步骤注入断连/退出，同键恢复后只能得到一项完整可执行任务，不得留下悬空映射。

## P2：可靠性与产品能力不足

### SR-16：多处全量读取与无限增长，会让使用时间和数据量放大成本

代码：[mysql_entity_store.py:52](E:/Data/codex/20260831rag/backend/src/ragkb/adapters/mysql_entity_store.py:52)、[mysql_upload.py:133](E:/Data/codex/20260831rag/backend/src/ragkb/adapters/mysql_upload.py:133)、[redis_queue.py:100](E:/Data/codex/20260831rag/backend/src/ragkb/adapters/redis_queue.py:100)、[tracing.py:36](E:/Data/codex/20260831rag/backend/src/ragkb/application/tracing.py:36)。

MySQL 虽已分实体行，但读一个会话/写一个事件仍加载整个租户实体并重建对象；Redis 每次 lease 会 HGETALL 所有历史任务后排序，并持有全局锁。文档/chunks API 无分页，前端全量渲染；InMemoryTracer 永久追加 spans。不能据此声称已经具备稳定的大数据量能力，本次未做压测。

解决方案：实体级查询/更新、追加式审计、数据库索引和游标分页；Redis 使用就绪/延迟/租约索引或 Streams，完成任务设保留期限；traces 使用有界环形缓冲、聚合指标及外部导出；前端分页或虚拟列表。

验收：用合成数据做本地无模型测试，确认单项操作读取行数及内存增长有界，不随全部历史数据线性增加。

### SR-17：前端共享可变选择状态，异步结果可能串库、串文档

代码：[App.vue:125](E:/Data/codex/20260831rag/frontend/src/App.vue:125)、[App.vue:154](E:/Data/codex/20260831rag/frontend/src/App.vue:154)、[App.vue:275](E:/Data/codex/20260831rag/frontend/src/App.vue:275)。

切库没有清空 lifecycle IDs、旧问答结果或取消在途请求；openDocument 更换版本却不清理旧 quality/review。上传轮询读取全局 lifecycle.versionId，而不是任务绑定的版本。用户在解析期间打开另一份文档，原任务完成后可加载另一份的质量报告。多个列表/问答请求也没有响应顺序保护，较慢的旧响应会覆盖新选择。

解决方案：每个请求捕获不可变的 space/document/version/job 上下文，以请求序号或 AbortController 丢弃过期响应；质量和审核结果必须带 version_id 并校验匹配。切库重置或分库保存视图状态，问答结果明确记录来源库；发布按钮依赖服务端 readiness，而非另一个文档遗留的 APPROVED。

验收：延迟响应下切库、快速切换文档、上传中查看其他文件、连续提问，检查页面标题、内容、质量和发布目标始终对应。

### SR-18：Production 分块列表缺失真实元数据，状态和数量可能误导

代码：[mysql_upload.py:311](E:/Data/codex/20260831rag/backend/src/ragkb/adapters/mysql_upload.py:311)、[vector_indexing.py:361](E:/Data/codex/20260831rag/backend/src/ragkb/adapters/vector_indexing.py:361)。

MySQL chunks 按哈希 ID 排序再临时编号，token_count 固定 None，kind 从没有完整保存的 node_type 推断。统计包括未写入向量库的 parent chunks。状态仅凭 current_version 就显示 SERVING，而撤权时 current_version 仍可能为 true，因此可能展示“已撤权但 Serving”。这不能准确回答“哪些块真正入库、按原文顺序是什么”。

解决方案：持久化 ordinal、kind、token_count、tokenizer/chunker revision、父子类型、vector_indexed 和生命周期；区分可检索子块与上下文父块计数。只有 lifecycle=SERVING、current=true 且水位满足才展示可检索，删除/撤权状态优先。

验收：同一文件在 Local/Production 的顺序、类型、token 数一致；发布、撤权、回滚后计数和状态可与真实索引对账。

### SR-19：OIDC 下点击引用链接不会携带 Bearer token

代码：[App.vue:573](E:/Data/codex/20260831rag/frontend/src/App.vue:573)、[rag.py:169](E:/Data/codex/20260831rag/backend/src/ragkb/api/routers/rag.py:169)、[api.js:16](E:/Data/codex/20260831rag/frontend/src/api.js:16)。

引用使用普通 target=_blank 的 a 标签。浏览器页面导航不会使用 authorizedFetch 添加 Authorization，source 接口却仍要求已认证 principal，签名 URL 也绑定用户。因此在 OIDC 模式下，问答成功后点击来源仍会 401。Local 免登录测试无法覆盖它。

解决方案：点击引用后通过 authorizedFetch 获取受保护内容并在应用内显示来源面板；或导航到应用内引用详情路由，由该页面加载 token 后读取。不要把长期 access token 放到 URL。

验收：OIDC reader 问答后点击来源可读；其他用户复制引用、退出登录、撤权后访问均被拒绝。

### SR-20：上传和模型等待缺少真正的端到端 deadline

代码：[uploads.py:176](E:/Data/codex/20260831rag/backend/src/ragkb/application/uploads.py:176)、[local_storage.py:150](E:/Data/codex/20260831rag/backend/src/ragkb/adapters/local_storage.py:150)、[model_http.py:162](E:/Data/codex/20260831rag/backend/src/ragkb/adapters/model_http.py:162)。

流式上传已有字节上限，但信号量排队和每次读取没有应用级等待上限，慢上传可长期占用全部槽位。不完整上传与崩溃残留也没有自动过期清理路径。模型 transport 的 with semaphore 会无限等待，HTTP 各阶段 timeout 不等价于整个请求墙钟上限；QA 四段调用也没有共用总 deadline。

解决方案：上传设置入场超时、流空闲超时、总时长和会话 TTL，退出清理临时文件并用定期对账清理崩溃残留。模型请求以剩余预算限时 acquire，并把统一 deadline 贯穿检索/生成/核验；断开或超时停止后续调用，明确排队拒绝码。

验收：合成慢流和被占满信号量按期释放；Generator 消耗大部分总时间后，Verifier 不再获得一份完整新超时预算。

### SR-21：Production 仍装配开发级恶意文件扫描器，实际解析缺少隔离

代码：[assembly.py:163](E:/Data/codex/20260831rag/backend/src/ragkb/runtime_profiles/assembly.py:163)、[malware.py:23](E:/Data/codex/20260831rag/backend/src/ragkb/engineering_security/malware.py:23)、[worker.py:200](E:/Data/codex/20260831rag/backend/src/ragkb/application/worker.py:200)。

所有 profile 都使用仅识别两种 EICAR 标记的 SignatureMalwareScanner，不能代表正式查毒。压缩包验证已在子进程实施资源限制，这项旧整改确实存在；但 PDF/Office 等实际解析仍直接运行在 Worker 进程中，缺少独立内存/CPU/墙钟限制。这里是防护能力不足，未声称已找到某个解析库的可利用漏洞。

解决方案：明确区分 disabled/development/production 扫描状态，Production 接入真实扫描端口并按配置失败关闭；实际解析在受限子进程执行，设置内存、时间、临时目录和网络权限限制，超时可终止。前端显示当前可用解析能力。

验收：超时/超内存/扫描服务不可用的合成文件被隔离，Worker 主进程持续可用；开发扫描器不得被标注为生产查毒通过。

### SR-22：缓存键仍未绑定 Verifier revision

代码：[qa.py:558](E:/Data/codex/20260831rag/backend/src/ragkb/application/qa.py:558)。

EvidencePackage 已有 verifier_revision，但 verified_answer_cache_key 没有加入。只改变 Verifier 版本时缓存键相同，已隔离复现。当前代码命中缓存后仍重新调用 Verifier，所以这不等同于直接绕过核验；问题是版本隔离、审计与“验证后缓存”的语义未闭合。

解决方案：缓存键加入 Verifier、权限策略及会影响生成上下文的版本标识。明确缓存的是草稿还是已验证结论；若复用结论，必须绑定证据 hash、权限 revision 和验证版本并做最终权限复核。

验收：更改 Verifier/策略 revision 必须使旧缓存不再命中；撤权与证据变化使缓存失效。

### SR-23：高风险 Production 模块仍排除在覆盖率外，测试缺少关键行为矩阵

代码：[pyproject.toml:77](E:/Data/codex/20260831rag/pyproject.toml:77)、[test_knowledge_base_api.py:12](E:/Data/codex/20260831rag/backend/tests/test_knowledge_base_api.py:12)、[test_mysql_normalized_state.py:19](E:/Data/codex/20260831rag/backend/tests/test_mysql_normalized_state.py:19)。

coverage 仍排除了 mysql_upload/lifecycle/governance/rag/references 和 low_cost_acceptance。新知识库测试主要覆盖本地单用户新文件成功路径；MySQL normalized 测试验证编解码和 SQL 形状，不能证明真实事务交错正确。上述 29 个测试全绿与权限/回滚问题同时存在，说明当前门禁不足以支持“功能已全部完成”的结论。

解决方案：移除高风险模块排除，分别报告总体与核心模块覆盖率；加入本报告的负向和失败注入用例。使用独立前缀的本地 MySQL/Redis 测试数据库，覆盖事务、断连、幂等和并发；真实模型保持关闭。前端补 OIDC 跨域、异步切换、非默认库新版本和引用点击测试。

验收：上述问题先写成会失败的行为测试，修复后通过；不依赖远端 CI，不把模型费用或大规模压测作为这些本地代码测试的前提。

## 建议实施顺序

1. 先封住读取权限与撤权输出窗口：SR-01、03、07；统一 SR-09 的实际权限变更。
2. 修复生命周期持久化和 Production 发布策略：SR-04、05、06。
3. 修复用户能直接遇到的完整流程：SR-02、08、17、18、19。
4. 修复索引/队列故障一致性：SR-10、11、14、15。
5. 处理阻塞、可观测性、数据增长及超时：SR-12、13、16、20、21、22。
6. SR-23 的行为测试随每项修复同步补齐；确认本地门禁通过后再声明对应问题关闭。

本次没有把已接受的单实例本地磁盘部署、可选价格配置、HTTP LLM 支持、远端 CI 失败、暂缓的 Gold/大规模真实验收当作新增缺陷。之前成功跑通的一条上传问答路径仍是有效的正向证据，但不能代替多身份、多知识库、多版本和故障恢复的验证。
