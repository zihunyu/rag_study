# G3 第三次审核结论

审核结果：`CHANGES_REQUIRED:G3_REVISION_3`

审核时间：2026-09-01T11:52:51+08:00

提交策略：`NO_COMMITS`。

## 已关闭的第二轮问题

- 已删除上下文在生成前被拦截，独立 tracking generator 调用数为 0；
- 已删除候选在 Reranker 前被拦截，tracking Reranker 文档数为 0；
- 默认 runtime `/ask` 已通过 Search-backed EvidenceProvider 返回本地合成 `answered`；
- SQLite 提交失败后内存和磁盘保持一致，权限 fail-closed；
- local original 由受控 executor 实际删除后才标记 COMPLETED；外部 cleanup 保持 PENDING_APPROVAL；
- Vue 引用 URL、POST SSE、verified 前隐藏答案和前端行为测试通过；
- 全量质量门通过：156 tests、Ruff 166 files、mypy 80 source files、OpenAPI 24 paths、SQLite v8、3 个前端行为测试、Vue build、npm audit、密钥扫描与 Docker 禁止项。

## 修订 3 仍需收口

### 1. P0：未发布文档默认可见，生命周期缺少 DRAFT/STAGED 状态

上传完成后没有创建 lifecycle record；`is_accessible()` 对未知记录默认返回 true。独立 reader 探针无需发布即可 `GET /api/v1/documents/{id}`，返回 HTTP 200，并可读取版本元数据和 `original_key`。

同时 `register_document()` 使用默认 ACTIVE/visible=true，发布前后没有真正的可见性边界。

修复标准：文档/版本创建时在同一事务或可靠 outbox 中建立 DRAFT/STAGED、visible=false 的 lifecycle 事实；reader 对未发布内容统一 404，maintainer/admin 走明确管理语义；只有 publish 完成权威事务后才进入 ACTIVE/visible。所有未知 lifecycle 状态必须 fail-closed，不能默认可见。

### 2. P0：发布/回滚没有更新 SQLite 权威文档与版本事实源

当前 publish/rollback 只更新 sidecar lifecycle tables。`document_versions.publication_state` 仍为 DRAFT，`documents.current_version_id` 不随发布/回滚更新，因此 API、生命周期与事实源可能互相矛盾。

修复标准：本地 SQLite publish/rollback 必须原子更新 lifecycle、documents.current_version_id、document_versions publication state、row version、audit、outbox 和 idempotency；故障回滚不得产生半发布。新增发布、第二版本、回滚和重启后的事实源一致性测试。

### 3. P0：未发布文档无法删除，重复删除/删除后撤权会回退状态

独立探针结果：

- 上传完成但未发布的文档调用 DELETE 返回 404；
- 删除并完成 local cleanup 后，用新的 Idempotency-Key 再次 DELETE，会把 local_file 从 COMPLETED 重置为 PENDING；
- 对已删除文档调用 revoke 返回 200，并把 lifecycle_state 从 DELETED 改为 REVOKED，虽然 tombstone 仍存在。

修复标准：DRAFT/PROCESSING/FAILED/ACTIVE/REVOKED 文档均可进入不可逆 tombstone；DELETE 的业务语义跨不同 key 也不得重置已有 tombstone/cleanup；已删除资源的 publish/rollback/ACL/revoke 必须 404/409 且绝不改变 DELETED/tombstone。覆盖重复删除、删除后所有命令和重启测试。

### 4. P0：local_file cleanup 遗漏 Worker artifacts

`LocalOriginalCleanupExecutor` 只删除 `document_versions.original_key`。独立探针创建与 Worker 规则一致的 `artifacts/canonical-document-v1.json` 后运行 local cleanup：original 已删除、状态为 COMPLETED，但 artifact 仍存在。

修复标准：建立可审计的本地内容血缘清单，至少覆盖 original、canonical artifacts、解析产物、媒体/图片、temp/quarantine 残留和允许清理的在线副本；逐项验证不存在后才能把 local_file 标为 COMPLETED。路径必须使用现有 containment 校验，禁止宽泛递归删除。

## 配置与外部边界

- 保持 `AUTH_MODE=local_single_user`，不等待企业 IdP；
- `APP_SECRET_KEY` 仍是跨重启引用配置待办；
- 不执行真实 MySQL G3 DDL、真实 LLM/ASR 或云端破坏操作；
- 不得进入 G4。
