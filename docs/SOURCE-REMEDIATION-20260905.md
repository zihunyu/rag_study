# 源码整改执行记录

对应审查：SOURCE-REVIEW-20260905.md，基准 5b80865。

本轮针对 SR-01～23 修改源码及回归用例。验证采用隔离本地数据库、合成权限主体和本地模型替身；没有调用真实模型、MinerU 或 Zilliz，没有执行现有业务数据库迁移、重启业务服务、修改 `config/.env`、提交或推送代码。远端 CI 不作为本轮门禁。

## 逐项整改

下表表示对应缺陷的代码处理，不等于已完成真实云端、生产部署或全部安全认证。验证范围及保留限制见后文。

| 编号 | 已实施的处理 | 本地证据 |
|---|---|---|
| SR-01 | 列表、版本、分块按实际 ACL、密级、有效期、当前版本复核；维护者必须有空间管理 scope；上传内容及任务读取/取消/重试也受管理范围限制。 | 同租户 reader 不能枚举草稿或读取受限正文；生命周期、引用 API 回归。 |
| SR-02 | 新版本从原文档读取不可变 space；MySQL/SQLite 仓储拒绝错误归属；读者列表选择当前发布版本而非最新草稿。 | 自建知识库版本会话测试；浏览器新建知识库上传链路。 |
| SR-03 | Generator 前后及 Verifier 后重新读安全控制面；校验 scope、clearance、generation、生命周期与 revision；失败不输出正文。 | Verifier 内撤权，结果不再是 verified=true；引用读取再次鉴权。 |
| SR-04 | MySQL 保存基于最初加载的 entity_revision，只写变更；禁止把旧快照缺少的记录解释为删除；生命周期内存操作加锁、刷新原子替换。 | 两个仓储交错注册/撤权，陈旧写入被拒绝，新记录不丢失；真实本机 MySQL 验证。 |
| SR-05 | Local/MySQL 共用发布纯策略；阻止占位解析、缺失投影、过期质量审核、错误文档归属和未确认投影。 | 发布策略负向矩阵、MySQL 审核 PENDING/APPLIED 重启测试。 |
| SR-06 | 发布事务同步目标 candidate=SERVING、原 candidate=RETIRED；提供历史候选对账迁移。 | 真实本机 MySQL 发布 v1→发布 v2→回滚 v1。迁移在临时库执行两次，第二次无重复操作。 |
| SR-07 | 审核记录与待同步投影同事务保存；只重放最新未确认的原始投影；旧审核幂等重放不再覆盖新权限；应用后持久化确认。 | A→B→重放 A、缺失投影确认、重启恢复用例。 |
| SR-08 | CORS 在鉴权之外处理 OPTIONS；实际请求仍要求身份；显式暴露 ETag、X-Request-ID。 | 强制 Bearer 验证器的预检 200/实际未认证请求 401；不同端口浏览器直连，无 Nginx。 |
| SR-09 | 权限接口接收真实策略及 If-Match；服务端生成 revision；先持久化不可 Serving 的迁移意图，再同步保留版本的投影，全部确认后恢复。 | 本机 MySQL 投影失败→阻止读取→重新装配→同键恢复；回滚后保留受限 ACL。 |
| SR-10 | MySQL 投影按 tenant/generation/chunk 复合身份保存；授权、发布和版本清理约束 generation；旧未绑定记录不猜测归属。 | 同 Chunk ID 的 g1/g2 正文互不覆盖，g1 撤权不改 g2；本机 MySQL 验证。 |
| SR-11 | 未知向量写入逐字段比对完整 payload、ACL、向量及 checksum，使用安全一致性读；完整向量记录进入 Saga manifest。 | 同主键但旧正文/ACL/校验值不得确认；批次集合及 checksum 原有回归。云端重放未执行。 |
| SR-12 | 阻塞业务路由改同步线程池边界；认证解码、上传元数据及磁盘操作移出事件循环；上传流仍保持异步限流。 | API、本地浏览器上传、Worker 链路及超时回归。 |
| SR-13 | 普通访问遥测使用每个应用独立的有界 1024 项队列，失败计数/丢弃，不改写业务响应；关闭连接池前限时收尾，空闲线程退出；live 不写日志库；安全审计仍走持久化业务路径。 | 遥测写入失败时 live 及业务请求不变成 500；队列满、关闭后拒收、退出等待和 Windows 临时 SQLite 文件释放回归。 |
| SR-14 | lease/3 持续续租；任务初始化纳入失败处理；队列 fence_token 在手动重试后仍递增；MySQL/SQLite 事务拒绝旧 token；原件派生产物和向量 PK 区分执行批次，检索拒绝过期批次；失租不再 fail 新作业账本。 | SQLite 接管/手动重试测试；真实本机 MySQL 第二 Worker 接管后旧 Worker 写入失败。已发送的远端请求不能保证服务商取消，见边界。 |
| SR-15 | Redis 事务原子保存任务、幂等映射、可执行索引及终态/DLQ 索引；悬空映射可恢复；PROMOTED 会话作为持久化入队意图，Worker 定期对账。 | 事务断连注入、16 路并发同键入队、本机 Redis 测试；上传完成中断及缺少生命周期注册的恢复。 |
| SR-16 | MySQL 单会话按实体读取，事件直接追加、诊断数据库聚合；治理写入只加载涉及集合；Redis 稳态不 HGETALL 历史任务；成功/取消任务保留 7 天、DLQ 30 天并分批清理；文档/分块分页及前端加载更多；trace 保留最近 2048 项。 | 单项实体读取范围、事务原子性、历史任务不参与租约扫描、tracer 容量测试。仍有管理型全量快照，不能宣称大规模容量已验证。 |
| SR-17 | 异步操作捕获库/文档/版本/作业上下文，以请求序号拒绝旧响应；切换清理质量/审核；新库创建完成前禁用上传；查看已解析文档自动读取其质量。 | 延迟旧库响应不会覆盖新库；真实浏览器发现并修复创建新库期间上传的竞态；完整流程回归。 |
| SR-18 | 投影持久化真实 ordinal/kind/token_count、父子关系、tokenizer/chunker revision；数量区分父块与可检索子块；撤权/退休/安全水位不足优先于 Serving 显示。 | 文档与分块 API 回归、本地实际索引列表、generation 投影测试。真实 Zilliz 页面与云端数量对账未执行。 |
| SR-19 | 引用经 authorizedFetch 在应用内读取；限制来源 URL 同后端 origin/路径；Token 不放 URL。 | 合成 OIDC Bearer 引用点击测试；浏览器引用接口读取；服务端主体和撤权校验。 |
| SR-20 | 上传排队/整体 300 秒、空闲 30 秒、会话 24 小时；临时流文件 1 小时和未完成隔离区文件 24 小时清理；模型 semaphore 受剩余时间限制，共享总 deadline；HTTP 使用可取消异步连接池。 | 慢流、排队占满、临时文件/配额释放、HTTP 整体取消、会话过期和原件保留测试。 |
| SR-21 | Production 使用已安装系统扫描引擎，失败关闭，不自动下载安装；实际原生解析使用资源受限独立进程和临时目录、禁用 Python 网络/子进程；未配置 ASR 在上传前明确拒绝；前端展示能力。 | 扫描器错误/不可用、原生解析成功/超时、ASR 拒绝测试；Windows 子进程资源限制。不是完整 OS 安全沙箱认证。 |
| SR-22 | 缓存键加入 Verifier revision 与权限策略 revision；缓存仍是草稿，命中后继续核验。 | 仅改变 Verifier revision，缓存键发生变化；撤权负向回归。 |
| SR-23 | 移除六个高风险模块 coverage 排除，门槛维持 70%；增加 SQL 行为矩阵及实际本机 MySQL/Redis 测试；更新隔离 OpenAPI 导出。 | 432 项通过、37 项真实产物/外部环境测试未执行；总体覆盖率 72.49%。核心模块单列如下，不以总体覆盖率掩盖薄弱模块。 |

## 本地验证

- Ruff：通过。
- Mypy：183 个源码文件通过。
- 前端：API/分块哈希 11 项、配置 1 项、Vue 6 项通过，Vite 生产构建通过。
- Playwright：2 项通过，其中 1 项不拦截业务接口，实际执行创建知识库、上传、Worker 解析与索引、质量审核、发布、问答和引用读取；模型为本地替身，不代表真实 Provider 验收。
- 后端本地测试集：432 passed，37 deselected，0 failed；耗时 153.23 秒。行及分支综合覆盖率 72.49%，门槛维持 70%。日志：`artifacts/source-remediation-pytest.log`；明细：`artifacts/source-remediation-coverage.json`。
- OpenAPI snapshot：已在临时数据库导出，与代码同步。
- 实际本机 MySQL/Redis 测试使用 `config/.env` 已有连接信息，但强制 localhost 并创建 `ragkb_sr_<随机编号>` 数据库和 `ragkb:sr-test:<随机编号>:` Redis 前缀。每次测试 finally 清理，清理结果写入测试输出。没有删除或改写业务数据。
- `git diff --check` 通过；浏览器测试的 18000 / 14173 端口服务已退出，没有重启用户的业务服务。

核心模块覆盖率（与本次总体覆盖率采用同一统计口径）：

| 模块 | 覆盖率 |
|---|---:|
| MySQL 上传 | 68.06% |
| MySQL 生命周期 | 80.93% |
| MySQL 治理 | 75.19% |
| MySQL RAG Run | 74.60% |
| MySQL 引用 | 75.56% |
| MySQL 检索投影 | 69.23% |
| MySQL 索引 Saga | 75.49% |
| 问答服务 | 86.59% |
| Worker | 82.14% |
| Production 装配 | 47.69% |
| 低成本真实验收执行器 | 27.68% |
| 解析进程资源限制 | 18.42% |

以上没有设“每个模块均达到 70%”的新结论。Production 装配和真实验收执行器尚有未覆盖路径；实际子进程成功/超时测试已运行，但子进程代码未并入主进程 coverage，因此进程限制模块数值偏低。仍有 Starlette/PyMySQL 弃用警告，不是测试失败，也不能当作依赖永远兼容的保证。

复现环境变量及命令：

```powershell
$env:APP_ENV='testing'
$env:RAG_RUNTIME_PROFILE='local'
$env:VECTOR_BACKEND='local'
$env:AUTH_MODE='local_single_user'
$env:REAL_PROVIDER_CALLS_ENABLED='false'
$env:EXTERNAL_LIFECYCLE_MUTATIONS_ENABLED='false'
$env:OTEL_ENABLED='false'
$env:RAG_LOCAL_DB_TESTS='1'
.venv/Scripts/python.exe -m pytest -m 'not integration' --cov=ragkb --cov-report=term --cov-report=json:artifacts/source-remediation-coverage.json -q
```

`integration` 分类的 37 项依赖真实 Provider 固定产物/外部环境，本轮不执行、不算通过。`mysql_sql_harness.py` 是有真实事务/行数语义的 SQLite SQL 测试替身，只能验证行为，不能代替 MySQL；本轮另外执行了真正本机 MySQL/Redis 测试。

## 升级现有业务环境前的必要步骤

1. 停止 Backend/Worker 写入，备份 MySQL、SQLite 及原件/解析产物；本轮没有擅自操作现有服务和业务库。
2. 执行 MySQL 迁移及 SQLite schema 19 升级。新 MySQL 主键和 ingestion fencing 表是新代码运行必需项，不可直接运行新 Worker 配旧 schema。
3. 旧 `retrieval_chunk_projections` 无法可靠反推 generation，迁移为 `legacy-unbound`，不会自动重新 Serving；应在备份后确认归属并重建/对账再发布。重建若调用真实 Embedding/Zilliz，需要计入真实预算，不在本次零模型调用测试之内。
4. 旧审核没有投影应用确认时不解锁发布；重新确认最新审核的安全投影。旧上传会话没有创建时间时不能无限复用，重新创建会话；原件不因该规则删除。
5. 前后端一起更新：权限 PUT 已改成 `security_projection + If-Match`，旧客户端传 revision/watermark 的请求不兼容。更新 OpenAPI 和客户端后再恢复流量。

## 不应被本轮结果掩盖的边界

- 本轮解决的是代码缺陷和本地行为验证，没有证明真实云端故障重放、Gold 业务签名、真实 OCR 攻击文件或大规模性能。模型/MinerU/Zilliz 调用数均为 0。
- 部署仍明确为单实例本地磁盘，不提供多实例/高可用保证。生命周期管理仍保留全租户快照装载；列表权限在应用层复核，尚不是完全下推到数据库的授权游标查询。这些剩余扩展性不足不能被“已有分页”包装成大规模能力。
- 资源受限子进程不是虚拟机/容器级的完整恶意代码沙箱；Python 审计钩子不是针对任意原生代码的 OS 网络隔离。扫描引擎的安装状态不等于签名库新鲜度/安全产品认证；本轮没有运行真实恶意文件认证。
- 客户端取消 HTTP、旧 Worker 失租会阻止后续本地提交，但无法保证已经到达外部服务的计算立即停止或不计费。旧向量执行批次通过独立 PK 和授权投影排除，不宣称分布式 exactly-once。
- 音频仍需要真实 ASR；现在明确拒绝而不输出占位成功，不伪造服务能力或业务审核签名。
- 质量阈值、真实调用硬预算、可选价格配置、HTTP/HTTPS 支持及用户允许忽略远端 CI 的边界没有放宽。
