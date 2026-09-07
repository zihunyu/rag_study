# RAGSPACE 知识管理工作台

前端已改为深色管理工作台。主导航为知识库、知识问答、任务中心、系统状态。沿用本地单用户方式，无登录入口、账户区域或前端 OIDC 初始化。已有后端权限约束、单轮问答接口及历史数据继续保留。

## 本地访问与运行

- 前端：<http://127.0.0.1:5173/knowledge-bases>
- 后端与接口文档：<http://127.0.0.1:8000/docs>
- 就绪探针：<http://127.0.0.1:8000/health/ready>

生产配置继续从 `config/.env` 读取。当前配置使用 MySQL、Redis、真实向量/模型服务及本地文件存储；不把示例模型当成生产服务。MySQL 和 Redis 应先启动。检查和启动项目自有进程：

```powershell
.venv/Scripts/python.exe scripts/local_stack.py status
.venv/Scripts/python.exe scripts/local_stack.py start
```

项目进程所有权记录在 `data/storage/temp/local-stack.json`。只操作经过所有权核验的项目进程，不按端口批量终止其他程序。现场运行日志位于 `data/storage/temp/service-logs/`。

前端开发服务将 `/api` 代理到 `127.0.0.1:8000`。最终构建在 `frontend` 目录执行 `npm run build`，默认 API 路径 `/api`。独立静态运行可设置 `FRONTEND_API_BASE_URL=http://127.0.0.1:8000/api`，再执行 `node scripts/serve-dist.mjs`；默认端口 8080，需将对应来源加入后端 CORS。已有反向代理部署应同时配置 SPA history fallback 和 `/api` 转发。不要将 E2E 的 18000 端口写入正式构建配置。

## 使用流程

1. 创建知识库，按需要填写描述。首页显示实际文档数、可问答数、待处理数及分块数，可搜索并切换卡片/列表。
2. 进入知识库上传文件。支持拖拽和多文件选择，同时上传 2 个文件。文件类型与大小来自服务端 capabilities；不支持的文件单独报错，其余文件继续。
3. 文件提交后，在任务中心查看真实处理状态、失败原因，执行重试或取消。刷新页面会恢复服务端任务；尚未提交的本地文件需重新选择，已保存上传会话可复用。
4. 文档详情中检查质量和解析内容。原文件、当前选中版本、分块位置关联一致；缺少来源页码时不生成推测页码。解析器仅返回全局质量问题时，页面说明没有逐项坐标。
5. 确认发布后，文档才参与检索与问答。批量发布前先加载每份文档的真实质量摘要。复核通过而发布失败时，保留复核阶段，重试继续发布。
6. “检索测试”展示命中文档、原文、召回渠道、排序与检索分数；分数不是答案正确率或置信度。文档操作菜单提供新版本、撤回、删除，版本历史可回滚。冲突时刷新当前状态后操作。
7. 知识问答中每个会话固定一个知识库。支持历史、追问、重命名、归档、停止、复制与反馈。点击引用查看文件、版本、实际位置和原文；可以打开对应版本并定位分块。

## 状态与恢复规则

文档管理默认显示全部未删除文档，不因草稿、失败或撤回而隐藏。解析状态与问答可用状态分别展示。共享查询服务结合最新版本、已发布版本、生命周期和检索投影计算 `availability`、`unavailability_reasons`、`available_actions`，首页和列表使用相同口径。

新版本尚未发布时，旧发布版本仍可问答，因此一份文档可以同时计入“可问答”与“待处理”。删除后的文档不在日常管理列表显示。统计不是各标签的简单相加。

上传幂等键、文件哈希及版本条件保留。已经提交的解析任务存储在服务端，浏览器关闭不取消任务。发布确认会在浏览器会话存储中保存一次逻辑操作的请求键，发布成功后清除；后续撤回再发布使用新操作键。

会话和轮次存入 MySQL（本地测试存入 SQLite）。提交事务保证同一会话只存在一个活动轮次，同一客户端请求 ID 不重复建轮次。SSE 断开后服务端任务继续执行，重新进入会话读取服务器状态。执行租约到期后标记中断，允许重试；取消后不展示迟到的答案。

上下文只使用最近 6 轮，最多约 6000 个保守估算 token。历史用于理解指代，不作为本轮事实证据；每轮重新检索当前资料。不明确的指代要求补充，答案在验证通过后才展示。历史引用重新检查生命周期、版本与访问有效性；已撤回、更新或无法校验的来源会隐藏旧答案与引用，提示重新生成。

## 配套接口

完整 schema 见 `docs/openapi/openapi.json` 和运行中的 `/docs`。

| 接口 | 用途 |
|---|---|
| `GET /api/capabilities` | 当前上传类型、大小上限与解析能力 |
| `GET /api/spaces/overview` | 知识库及统一统计 |
| `PATCH /api/spaces/{id}` | 名称、描述 |
| `GET /api/spaces/{id}/documents/preview` | 管理列表；q、processing、availability、sort、cursor、limit，默认 30 条 |
| `GET /api/spaces/{space}/documents/{document}/workspace` | 管理详情、版本、质量、生命周期与发布阶段 |
| `GET /api/document-versions/{id}/original/preview` | 管理范围内的原文件下载 |
| `POST /api/document-versions/{id}:review-and-publish` | 幂等复核发布；阶段 pending → reviewed → published |
| `GET /api/ingestion-jobs` | 知识库/状态筛选、游标分页的处理任务 |
| `GET /api/ingestion-jobs/summary` | 实际保留任务统计 |
| `GET /api/system/status` | 真实依赖探针、调用记录、未检测原因 |
| `POST /api/conversations` | 创建固定知识库的会话，使用 Idempotency-Key |
| `GET /api/conversations` | 按知识库筛选会话与游标分页 |
| `GET /api/conversations/{id}` | 最近 50 轮；before 向前读取完整历史 |
| `PATCH /api/conversations/{id}` | 重命名或归档 |
| `POST /api/conversations/{id}/turns:stream` | question、client_request_id；SSE turn/result 事件 |
| `GET /api/conversations/{id}/turns/{turn}` | 刷新/断线后的轮次状态 |
| `POST /api/conversations/{id}/turns/{turn}:cancel` | 停止当前轮次 |

列表游标由 `X-Next-Cursor` 返回；筛选在分页前执行，游标绑定查询范围。Redis 维护租户/知识库/状态索引，历史索引在同一写锁下幂等回填，列表请求不扫描整个队列。完成、取消任务保留 7 天，失败任务保留 30 天；文档与版本另外持久化。

## 增量迁移与历史修复

MySQL 原迁移 ID 保留，新增四张工作台/会话表、三个索引，以及 `widen_governance_event_ordinal`：将事件排序字段从 INT 扩展为 BIGINT，容纳微秒时间戳。后者修复真实运行中的 MySQL 1264 越界错误。所有表增量迁移，不清空旧记录；SQLite 使用对应的追加 schema。

当前现场数据迁移到保留历史的 `rag_kb_current_20260906`，原 `rag_kb` 库保留。迁移、备份与前后行数报告见 `artifacts/redesign/database-migration.json`、`telemetry-migration.json` 及 `artifacts/service-startup/database-migration.json`。旧单轮问答记录未猜测会话归属。

历史修复工具默认只诊断：

```powershell
.venv/Scripts/python.exe scripts/repair_publications.py --output artifacts/history-diagnosis
```

明确执行时加入 `--apply` 并使用新的输出目录。工具核对 APPLIED 发布快照、复核、版本、索引清单、投影、实际向量与后续生命周期；只补证据一致的缺失状态或能够证明为启动恢复创建的草稿。写入前备份，使用行锁和版本条件，重复执行无修改；已有修复备份不会被覆盖。撤回、删除及后续生命周期操作禁止恢复。

现场已恢复 4 份文档。其余 51 份的名称、状态和原因见 `artifacts/redesign/HISTORY_REPAIR.md`。完整备份对比证明：除指定 4 份外，其他 52 条生命周期记录未变，包括原有 REVOKED 记录。重复修复结果 changed 为空。

## 验收与当前限制

隔离测试覆盖上传部分失败、队列恢复/取消/重试、质量发布、版本回滚/撤回、引用失效、会话隔离、幂等与答案验证。前端还验证批量质量摘要加载、旧版本引用跨 100 个分块定位、刷新按钮和发布重试请求键。Playwright 检查 390、1024、1440 像素布局。隔离运行使用测试模型，不等同于真实模型效果验收。

2026-09-07 最新真实服务复验已通过：生成模型 gpt-5.5，核验模型 gpt-5.4-mini，本次未出现 429。名称查询、血量多轮追问、CSV 条件区分和 Word 合同缺项回答均有真实结果和可访问引用。通用分块、证据选择、有限补查与旧资料重处理说明见 [RAG_GENERAL_REPAIR.md](RAG_GENERAL_REPAIR.md)，最新现场证据见 `artifacts/redesign/GENERAL_RAG_REPAIR_DELIVERY.md`。旧失败轮次保留，刷新后发送新问题即可重新验证；不要重用旧失败轮次的请求键。

系统状态只把实际检测或成功调用记为正常。Worker 暂无独立心跳探针；MinerU 配置存在不代表远程调用已经探测。页面明确标为“未检测”。未增加 ASR、在线数据源同步、可视化流程编排、每库模型配置或跨知识库联合问答。
