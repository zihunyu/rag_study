# 前端重设计续接状态（2026-09-07）

## 最新反馈修复：答案整理（2026-09-07）

用户指出回答反复列字段、缺少综合总结。已定位并移除新格式答案的两处事实清单重建：阅读用 Markdown 与核验清单分开，增加整篇正文及引用核验，验证通过才发布原样正文。新生成 revision 为 `synthesized-markdown-v5`，缓存保存 synthesized 标记；旧格式继续兼容。技术说明 `docs/ANSWER_SYNTHESIS.md`，交付记录 `artifacts/redesign/ANSWER_SYNTHESIS_DELIVERY.md`。

最终五轮真实问答通过，当前可见会话 `226423a4f49ab28033610e43e01a7d30ad05`（古达、血量、NG+）和 `c576231847bf61aef3c38b09e46e972e35f8`（设备、合同）。三视口段落/表格与行内引用检查通过。本轮回归 105 项、配套 46 项通过，前端增加到 28 项；mypy 194 文件、Ruff、构建及 OpenAPI 检查通过。服务保持运行；没有修改原文档版本。本轮失败调试会话已归档保留，旧用户会话未改写。以下为更早的通用 RAG 修复记录。

## 最新状态：通用 RAG 修复已交付（2026-09-07）

用户已要求并完成通用修复。本节覆盖下方所有旧暂停、429 阻碍及待交付描述；下方保留原始阶段记录供追溯。

- 实现结构化表格/标题/真实位置分块、语义证据选择、一次最多两条补查、名称查找、部分回答提示以及匿名数字误判修复。源码说明：`docs/RAG_GENERAL_REPAIR.md`。
- 6 份已发布旧文件备份后以新版本重处理并发布，旧版本保留；50 份排除资料生命周期逐项未变。当前 58 份管理文档、8 份可问答、50 份待处理（4 待复核、45 失败、1 取消）。清单与结果：`artifacts/redesign/GENERAL_RAG_REPAIR_DELIVERY.md`、`GENERAL_RAG_UNAVAILABLE.md`。
- 真实对话 `e08fdac3d325ea5782fc47d9db0073321387`：古达 → 它的血量是多少 → NG+呢，三轮 verified=true，引用均可访问。跨格式对话 `2ea8ff8768afc658a84b9c6fd02acddba045`：设备 CSV 和合同 Word，四轮验证通过，缺失热线不编造。
- 生成 gpt-5.5／核验 gpt-5.4-mini，最后复验没有 429。前端 5173、后端 8000、Worker 均由 local_stack 管理并运行，ready/live/前端 HTTP 200。最终健康快照 `general-rag-final-health.json`。
- 新增通用回归 14 项；数字/冲突等相关回归 162 项通过；前端 27 项、E2E 5 项通过；三视口真实页面检查通过。全量后端首轮 861 passed、3 failed、4 skipped、8 deselected，三项分别为配置环境检查、取消测试就绪文件竞态和旧 OpenAPI 快照；配置在实际环境单测通过，其余相关重跑 20 项通过，详情见交付记录，不把首次全量报告描述成零失败。
- Ruff、mypy、OpenAPI 快照检查及正式前端构建通过；dist 未包含 E2E 18000/14173 地址。本轮不新增数据库迁移。
- 所有源码仍未提交，保留之前重设计和本次通用修复改动。旧失败回答保持历史，不会被新实现覆盖。

## 最新模型复验（2026-09-07）

用户更换生成配置为 gpt-5.5，并明确指定核验模型 gpt-5.4-mini。已备份并更新 config/.env 的 VERIFIER_MODEL；G4 启动检查通过，后端与 Worker 已加载新配置。真实合成知识库的两轮问答均 verified=true，各有一条可访问原文引用；追问成功解析为星云 X1 的上门维修问题。本次未出现 429。前端与后端 ready 均 HTTP 200，生成与核验供应商连续失败次数均为 0。证据：artifacts/redesign/real-qa-recheck.json 和 model-recheck-health.json。以下旧限流结论保留为历史记录，以本节为准。

用户已于 2026-09-07 要求继续。本次已恢复并完成可独立处理的收尾工作。**当前仅真实模型问答验收受外部供应商限流阻挡；不再处于用户要求暂停的状态。**

- 新前端保持运行：`http://127.0.0.1:5173/knowledge-bases`。完整使用、接口、迁移说明在 `docs/FRONTEND_WORKSPACE.md`。
- 遥测 MySQL 1264 已修复：追加 `widen_governance_event_ordinal`，INT 扩为 BIGINT，20 项迁移中本次只应用 1 项；真实事件写入成功。迁移前完整备份及计数在 `artifacts/redesign/telemetry-migration.json`。
- 历史保护核验已修正：用完整 SQL 备份通过临时表比对全部 56 条原生命周期记录，只有 4 个指定目标改变，其余 52 条均未变，包括 REVOKED 的 qa-document。更新 `artifacts/redesign/HISTORY_REPAIR.md` 和 `history-repair/final-status.json`，不再用空集合宣称保护核验通过。
- 补齐发布阶段刷新恢复、历史版本文件名、原文件下载名、系统刷新按钮事件问题；修复确认生命周期操作期间切换文档导致操作对象变化的竞态，操作绑定发起时文档与版本。
- 前端单元测试增加到 15 项，加上 API/hash 11 项和 config 1 项合计 27 项。新增覆盖批量质量、旧版本跨分页引用、刷新、发布幂等键、跨文档操作竞态。E2E 5 项通过。
- 上次全量后端 849 passed；本次相关后端 41 passed（含新增迁移）、加强后的多轮测试 6 passed。Ruff/mypy 通过。离线多轮模型为受控测试实现；真实服务结果另记。
- 真实前端在 390/1024/1440 三个视口检查 6 类页面，共 18 项，零脚本错误、零横向溢出；`artifacts/redesign/real-browser-final.json`，截图 `workspace-{width}-{index}.png`。
- 真实模型复验仍失败：供应商原文为 **All available accounts are currently rate-limited. Please retry later.**，HTTP 429，无 Retry-After。记录在 `artifacts/redesign/model-limit-diagnostic.json` 与 `real-qa-recheck.json`；新复验会话 ID 在后一文件。原文件处理、发布、混合检索真实验收已通过。未展示未验证答案，未切换为假模型，未更改供应商配置。
- 没有创建 git 提交，所有改动仍在工作区。不要丢弃。

继续验证真实问答时，应先解决供应商限流；仅使用专用合成验收库。`artifacts/redesign/real_qa_recheck.py` 会在原测试库创建独立会话，使用新的逻辑请求键；成功时验证两轮回答和引用。

以下为 2026-09-06 暂停时的原始记录，保留用于追溯；其中“未完成”项以本节当前状态为准。

---

更新时间：2026-09-06 22:55（Asia/Hong_Kong）。用户明确要求“记住当前状态，先暂停”。**在用户要求继续之前，不继续修改、修复、测试或调用外部模型。现有服务保持运行。**

## 目标与约束

按已批准计划实现深色、管理优先、无登录的 RAG 知识库工作台，包括配套接口、多轮对话、历史数据诊断修复、真实服务启动及验收。保留 Vue 3/Vite/RAG 核心；使用 Vue Router、Pinia；无前端 OIDC 初始化、账户或登录入口。原 `/api/ask` 保持兼容。不要猜测旧问答会话归属，不重新建立空库，不恢复已撤回内容。没有创建分支或提交；当前未提交改动均需保留。基线 HEAD：2afd62f26075159a9fdeb3b04c12166730d93518。

工作区：`E:\Data\codex\20260831rag`。Windows PowerShell，`.venv/Scripts/python.exe`。不要输出 `config/.env` 密钥，不要关掉不属于项目的进程。不要启动子代理（当前指令未授权）。

## 已实现

- 全新石墨黑/青蓝前端，侧栏导航：知识库、知识问答、任务中心、系统状态。页面/组件/API/状态管理已拆分；移动端导航和来源面板收起。
- 知识库真实统计、名称搜索、卡片/列表、新建与设置；管理文档默认显示草稿和失败文档；服务端搜索、筛选、排序、游标分页。
- 多文件拖拽上传并发 2，逐文件哈希、实际字节进度、部分失败、重试；提交后的任务从服务端恢复；本地文件丢失明确提示重新选择。
- 文档质量、解析内容、分块导航、版本历史、原文件下载、复核发布、新版本、撤回、回滚、删除。内部 JSON 收入技术详情。引用链接携带实际版本和分块，跨分页定位。
- 批量发布先加载真实质量摘要。`confirmPublication` 为一次逻辑操作保留会话存储中的请求键，发布成功后清除，避免撤回后再次发布被旧幂等键吞掉。
- 新 workspace API：overview、PATCH space、document workspace、原文件预览下载、review-and-publish、任务列表/统计、system/status。
- 后端统一 availability/reasons/actions。Redis 查询索引由队列写入事务维护，一次幂等回填，查询不重复扫描整个队列。
- 持久化会话/轮次（MySQL 与 SQLite），单会话单轮运行、客户端请求 ID 幂等、SSE、取消、断线恢复、执行租约。最近 6 轮/最多约 6000 保守 token 理解上下文，每轮重新检索；验证通过才展示；历史引用重新校验，失效时隐藏旧答案与引用。
- `ConversationService` 已移到 `backend/src/ragkb/infrastructure/conversation_service.py`，满足 application 不能导入 infrastructure/adapter 的架构边界。
- 新增工作台增量迁移，OpenAPI 快照已重新导出。
- 实现 `scripts/repair_publications.py`：默认干运行、证据核验、备份、按文档恢复、禁止后续生命周期覆盖、重复执行无修改。纯判断还拒绝空核验集合。

## 运行与数据

- 前端： http://127.0.0.1:5173/ ，后端： http://127.0.0.1:8000/ 。MySQL 3306、Redis 6379、本地 backend/worker/frontend 均启动。
- 所有项目服务通过 `scripts/local_stack.py` 的所有权记录管理：`data/storage/temp/local-stack.json`。不要只按端口或 PID 杀进程。
- `artifacts/redesign/restart_services.py` 验证 PID/创建时间/所有权后仅重启 backend、worker；`restart_frontend.py` 仅重启 Vite；`artifacts/service-startup/start_missing_services.py` 隐藏启动缺失服务并记录日志。
- Vite 曾因依赖安装后旧优化缓存返回模块 504，**22:53 已安全重启前端并验证解决**。真实 Chrome 无头浏览器访问首页、文档详情、会话、系统状态全部成功，无控制台错误，无横向溢出。旧浏览器页需要刷新。
- 当前生产数据库：`rag_kb_current_20260906`。此前旧 `rag_kb` schema 不兼容，已保留原库并迁移历史数据；这不是空库。此前迁移记录：`artifacts/service-startup/database-migration.json`。
- 原库迁移前备份：`data/storage/temp/service-backups/20260906-204447/`。工作台增量迁移前完整当前库备份：`data/storage/temp/redesign-backups/20260906-222031/database-before-workspace.sql`。
- 工作台迁移报告：`artifacts/redesign/database-migration.json`；原 12 项迁移保留，追加 7 项（共 19 项）；原 59 条单轮 RAG 历史保持。
- 原 55 份文档：4 份恢复可问答，5 份待复核，45 份处理失败，1 份取消。4 份恢复的文件：rag-smoke-2026.txt、rag-smoke-en-2026.txt、web-full-flow-20260904.md、knowledge-base-ui-20260904.md。
- 历史修复报告：`artifacts/redesign/HISTORY_REPAIR.md`；机器报告及备份：`artifacts/redesign/history-repair/{dry-run-before.json,applied.json,repair-before.json,final-status.json}`。重复执行记录：`history-repair/idempotency-check/applied.json`，changed 为空。
- 当前另有专用合成验收库“重设计验收库-20260906”，使总数为 3 库/56 文档/5 可问答/51 待处理。库 ID `01a07725-8115-7471-9727-07bdb5ec9cde`；文档 `01a07725-818a-7f6b-8c2a-d4a370d6f1ca`；版本 `01a07725-818b-7efe-8aa4-13ac4f81abd7`。真实解析、质量、发布与混合检索已通过。

## 验证结果

- 后端全量隔离回归：**849 passed, 4 skipped, 9 deselected**，211.67 秒。`artifacts/redesign/backend-tests-final.xml`。其中实际部署配置检查需独立执行，已在真实环境单独通过（1 passed）。跳过/排除包含真实服务显式 opt-in 测试，不能说所有外部验收均通过。
- 前端 `npm test`：22 passed（11 API/hash、1 config、10 UI）。最后一次之后仅有截图相关 E2E 测试调整，生产代码无未测试行为变更。
- Playwright E2E：**5 passed**，覆盖多文件部分失败、处理恢复、质量发布、两轮追问、引用、新版本、回滚、撤回后历史引用隐藏、任务取消重试、390/1024/1440 视口。
- Ruff 与 mypy 已通过；最后部分小修改后应再做最终格式/lint 检查。不要为了格式重写无关源文件。
- 真实运行前端浏览器检查：`artifacts/redesign/real-browser-smoke.json`，4 页面无错误/溢出；截图 `real-page-0.png` 到 `real-page-3.png`。E2E 截图在 `frontend/test-results/`，包括来源侧栏及三个尺寸首页。

## 未完成与已知阻碍（继续时优先处理）

1. **真实模型调用持续 HTTP 429 限流**。真实会话第一轮状态 failed、warning QUESTION_ASSESSOR_UNAVAILABLE，未展示答案。直接同配置适配器探测为 ProviderRateLimited / MODEL_PROVIDER_RATE_LIMITED；供应商未返回 Retry-After，错误内容属于 rate/capacity，无额度/余额特征。不能声称真实多轮问答验收通过，也不要降级为假答案。记录：`artifacts/redesign/real-service-acceptance.json`、`model-limit-diagnostic.json`。会话 `0880ba0e0e2b9199cc25449602e0bd189bd9`。恢复后重试必须使用新的逻辑 client_request_id；旧请求键会返回原失败轮次。原验收脚本 `real_service_acceptance.py` 有固定库名与请求键，不要直接重复运行撞库名或重放旧失败，应复用已创建合成资源做新轮次。
2. **刚定位到旧访问遥测写入失败**：日志 ACCESS_TELEMETRY_UNAVAILABLE；直接 MySQLGovernanceRepository.record_event 合成诊断返回 `DataError`，MySQL 错误号 **1264（数值越界）**。尚未查看具体字段、尚未修复。可能检查 `backend/src/ragkb/adapters/mysql_governance.py` / mysql_entity_store / migrations 的 ordinal 或时间字段，但目前只是待查方向。主业务和新 API 不受此异步日志错误阻断。
3. 当前 `HISTORY_REPAIR.md` 的“已撤回或删除状态逐项保持不变：True”来自 `all([])`，**不能作为真实逐项核验的证据**：新报告 protected 数组为空，筛选只检查 REVOKED/DELETED/tombstoned，旧记录可能是其他枚举/嵌套结构。继续时应修正报告：对修复前后所有 lifecycle document payload 做比较，除明确恢复的 4 份之外均应相等；必要时说明没有命中保护状态。代码纯测试已覆盖撤回、删除、后续操作的拒绝恢复，实际工具也只更新 4 个目标。
4. 最终交付文档尚未完成。应补 `docs/FRONTEND_WORKSPACE.md`（运行、接口、恢复、限制）以及最终验收说明；包括真实 429 未通过项、历史 51 份清单、备份路径。
5. E2E 构建过 `frontend/dist` 时使用 API 18000；**最终交付前在没有 E2E 环境变量的新进程运行 `npm run build`**，生成默认 API 配置的正式 dist。目前运行 Vite 用 8000，不受影响。
6. 最终刷新页面并核对服务状态；浏览器 viewport 需恢复默认。若继续修改生产代码，运行相关测试，不必无变化反复全量测试。
7. 可补针对新批量质量预览、引用跨版本/跨 100 个分块导航、撤回后重新发布请求键的针对性测试；已有 E2E 通过基础生命周期与旧引用失效。质量报告目前只提供全局 issue_codes，前端诚实提示缺少逐项坐标，不伪造问题页码。

## 继续用的命令与文件

```powershell
.venv/Scripts/python.exe scripts/local_stack.py status
.venv/Scripts/python.exe artifacts/audit-20260906/run_offline_tests.py backend/tests -q -k 'not test_actual_config_parses_without_exposing_values'
.venv/Scripts/python.exe -m pytest backend/tests/test_env_config.py::test_actual_config_parses_without_exposing_values -q
.venv/Scripts/python.exe -m ruff check backend/src/ragkb
.venv/Scripts/python.exe -m mypy backend/src/ragkb --no-error-summary
.venv/Scripts/python.exe scripts/export_openapi.py
```

前端工作目录 `frontend`：`npm test`、`npm run build`。E2E 设置 `RAGKB_E2E_API_PORT=18000`、`RAGKB_E2E_WEB_PORT=14173`、`RAGKB_E2E_BACKEND=E:\Data\codex\20260831rag\.venv\Scripts\ragkb-backend.exe`、`RAGKB_E2E_WORKER=E:\Data\codex\20260831rag\.venv\Scripts\ragkb-worker.exe` 后 `npm run test:e2e`。不要让这些测试参数写入部署配置。

新增主要后端文件：`infrastructure/{workspace_schema,workspace_db,workspace_queries,conversations,conversation_service,publication_repair}.py`，`api/routers/{workspace,conversations}.py`，`adapters/{conversation_context,redis_queue_browse}.py`。前端：`src/{router.js,format.js,App.vue}`、`pages/`、`components/`、`stores/`、`styles/workspace.css`。未提交改动请用 git status 查看并保留。
