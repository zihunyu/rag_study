# G3 第四次审核结论

审核结果：`CHANGES_REQUIRED:G3_REVISION_4`

审核时间：2026-09-01T12:11:19+08:00

提交策略：`NO_COMMITS`。

## 已关闭的第三轮问题

- reader 对 DRAFT 文档和版本返回 404，maintainer/admin 管理读取为 200；
- DRAFT 文档可直接 tombstone；
- 重复 DELETE 使用不同 key 也不会重置 cleanup；
- DELETED 后 revoke 返回 409，状态保持 DELETED+tombstone；
- publish/rollback 会同步 SQLite documents/document_versions/lifecycle；
- local_file cleanup 同时删除 original 和 Worker canonical artifact，独立探针确认均不存在；
- 全量质量门通过：161 tests、Ruff 168 files、mypy 80 source files、OpenAPI 24 paths、SQLite v9、3 个前端行为测试、Vue build、密钥扫描和 Docker 禁止项。

## 最后必须修复

### 1. P0：PROCESSING/DRAFT 版本可以绕过 Worker 与索引就绪直接发布

独立探针在上传完成后、Worker 尚未运行时调用 publish。此时 version processing state 为
`PROCESSING`、lifecycle 为 `DRAFT`，接口仍返回 HTTP 200，并把 version publication state 改为
`SERVING`。

这绕过完整计划 10.5 的发布协议：解析/Chunk/Embedding/索引必须完成，投影处于 STAGED，
watermark/Strong 验证通过后才能切换 current version。当前行为可能把无 Chunk、无索引或失败版本
发布给 reader。

修复标准：

- publish 前要求目标 version processing=`VALIDATED`、lifecycle candidate=`STAGED`，并通过可注入的 index/retrieval release readiness port；PROCESSING/FAILED/QUARANTINED/CANCELLED/DRAFT 必须 409，且不得写 audit/idempotency/事实源；
- 本地开发使用确定性 readiness adapter，不执行真实 Zilliz；测试覆盖 ready/not-ready/watermark/代际不匹配；
- 发布过程至少以本地状态机模拟 `SWITCHING → projection swap → CAS current_version → ACTIVE`，失败保持旧版继续服务；不能只改 SQLite 指针；
- Worker 直接把 DB 状态改为 STAGED 后，API 发布检查必须读取最新权威状态，不能依赖进程启动时缓存；
- 同一已发布版本即使用新的 key 重复发布，也应稳定 no-op 或明确冲突，不重复增加 row version/audit。

### 2. P1：缺少可用的新版本创建/更新流程，回滚测试依赖直接写数据库

当前上传 API 每次创建新 Document，正式 API 没有把新上传绑定到既有 Document 的版本创建流程。
两版本 publish/rollback 测试通过直接 INSERT `document_versions` 构造第二版，因此管理端用户无法通过
正常 API 完成“上传新版本 → Worker 验证 → STAGED → 发布 → 回滚”。

修复标准：提供幂等、带权限和并发控制的新版本上传/创建契约，保持版本不可变与 version_no 唯一；
Vue 管理端接入该流程；新增不直接写业务表的 API E2E，覆盖旧版继续服务、新版失败不切换、成功发布
及回滚。真实格式内容仍使用合成 Fixture，不触发 G4 样本测试。

## 配置与外部边界

- 保持 `AUTH_MODE=local_single_user`，不等待企业 IdP；
- `APP_SECRET_KEY` 仍是跨重启引用配置待办；
- 不执行真实 MySQL G3 DDL、真实 LLM/ASR 或 Zilliz 发布/删除；
- 不得进入 G4。
