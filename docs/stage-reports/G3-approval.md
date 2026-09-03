# G3 本地开发审核批准

结论：`APPROVED:G3_LOCAL_DEVELOPMENT_NO_COMMITS`

审核时间：2026-09-01T12:36:11+08:00

## 独立证据

- 生成器前后完整上下文权限复核通过；已撤权上下文的 generator 调用数为 0；
- 删除/撤权候选在 Reranker 前剔除，tracking Reranker 文档数为 0；
- DRAFT/STAGED 对 reader 为 404，maintainer/admin 管理读取正常；
- 生命周期、tombstone、audit、idempotency、reference、cleanup outbox 和本地内容血缘均为 SQLite 持久状态；
- 删除后重启仍不可见；重复 DELETE 不重置 cleanup；DELETED 后命令不能回退终态；
- local cleanup 实际删除 original 与 Worker canonical artifact 并验证不存在，外部 cleanup 保持 PENDING_APPROVAL；
- PROCESSING/FAILED/QUARANTINED/CANCELLED 和 generation/watermark/checksum not-ready 发布均返回 409 且无事实源、audit 或 idempotency 副作用；
- Worker 完成后相同 publish key 可重试成功；同一当前版本新 key 发布为稳定 no-op；
- 独立正式 API E2E：v1 upload→Worker STAGED→publish；v2 upload→early reject 且 v1 继续 serving→Worker STAGED→v2 publish→v1 rollback；最终 version/candidate/current pointer 一致；
- SQLite SWITCHING/projection swap/CAS 注入失败时整体回滚，旧版继续服务；
- Vue 3/Vite 引用 URL、POST SSE verified 边界、新版本上传和本地 cleanup 交互通过；
- 全量质量门：Python 3.12.13；pytest 174 passed；Ruff 171 files；mypy 81 source files；OpenAPI 25 paths；SQLite schema v10；G3 eval 6/6；3 个前端行为测试和 Vue build；npm audit、密钥扫描、Docker 禁止项全部通过；
- Git HEAD 未变化，`config/.env` 未跟踪且未输出值。

## 批准边界

本批准只表示 G3 本地开发、确定性 Mock、合成 Fixture 和本地持久化验收通过，不表示企业真实 G3 Gate 通过。以下仍保持阻断：

- `APP_SECRET_KEY` 尚未配置；
- 用户确认暂时没有企业 IdP，`enterprise_oidc_acceptance=false`；
- 真实 LLM 合成探测未获计费授权；
- MySQL G3 9 项 DDL 未获真实执行授权；
- 真实 Zilliz/Redis/MySQL 撤权、删除、恢复与 cleanup 演练未获授权。

继续 `NO_COMMITS`。允许进入 G4 的本地 Harness、全格式加固和系统验证准备，但不得宣称 Release Candidate 或执行未经批准的真实样本、ASR、LLM、MySQL G3 DDL或云端破坏操作。
