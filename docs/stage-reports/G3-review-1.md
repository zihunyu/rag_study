# G3 第一次审核结论

审核结果：`CHANGES_REQUIRED:G3`

审核时间：2026-09-01T10:29:21+08:00

提交策略：`NO_COMMITS`。

## 已通过

- G3 配置报告为 `gate_ready=true`；ASR 三项仍仅属于 G4。
- 全量质量门通过：pytest 135 passed、Ruff 150 files、mypy 72 source files、OpenAPI 22 paths、SQLite v6、npm、原生入口、密钥扫描和 Docker 禁止项均通过。
- 六种业务状态、SSE 先验证后结果、引用 ID 结构、反馈 revision 绑定和合成评测 Harness 的正常路径已实现。
- 未调用真实 LLM/ASR/MinerU 文档或新增 Embedding/Reranker；未执行云端破坏性操作；Git HEAD 未变化。

## 必须修复

### 1. P0：输出前权限复核没有覆盖全部生成上下文

`TrustedQAService.ask()` 只把 `draft.citation_ids` 对应的片段传给最终权限复核。未被引用、但已经送入生成器并可能影响答案的证据若在生成期间撤权，当前实现仍会返回 `answered`。

独立探针已复现：E1/E2 同时送入生成器，生成器仅引用 E1，最终权限适配器拒绝 E2；当前结果仍为 `answered`。

修复标准：输出前对本次送入生成器的完整 evidence/context 集合重新执行租户、用户、ACL revision、current version、有效期和 locator 复核；任一失败必须丢弃缓冲答案。新增“未引用上下文在生成期间撤权”的负向回归。

### 2. P0：删除与 tombstone 仅在内存中，删除后仍可读取并在重启后消失

生命周期运行时使用 `InMemoryLifecycleStore`；SQLite v6 虽创建了 lifecycle/tombstone/audit 表，但没有 Repository 接线。`DELETE /api/v1/documents/{id}` 只改内存，不更新上传事实源、文件可见性或所有读取路径。

独立探针已复现：删除接口返回 DELETED 后，`GET /api/v1/documents/{id}` 仍返回 HTTP 200；用同一 SQLite 和本地存储重建运行时后 tombstone 不存在，文档仍返回 HTTP 200。

修复标准：使用事务性 SQLite 本地适配器持久化 lifecycle、transition、tombstone、idempotency、cleanup 和 audit；所有文档/版本/检索/问答/预览/下载路径先检查持久 tombstone 与最新权限状态并 fail-closed；重启恢复和旧快照重放不得复活。真实 Zilliz/Redis 删除仍不得执行，但必须以持久 outbox/cleanup 状态建模。

### 3. P0：ACL 完成操作不幂等，重复请求会破坏已验证状态

权限 API 对相同 `Idempotency-Key` 再次调用时，会取回旧 transition 后再次调用 `complete_acl_transition()`。记录此时已经 ACTIVE，该函数将 transition 改为 FAILED 并把 `visible=False`。

独立探针已复现：第一次完成后 `accessible=True`；完全相同请求重试后 `accessible=False`。

修复标准：幂等唯一范围至少包含 tenant/operation/key，并保存 request hash 与稳定响应；同键同请求直接返回原结果，不重复完成；同键不同请求返回 409；失败校验不得提前消费 key。覆盖 publish/rollback/ACL/revoke/delete/cleanup 的重复、冲突和重启回归。

### 4. P1：引用 Token 未绑定租户/用户且映射重启丢失

当前引用 payload 只绑定 run/evidence/expiry，缺 tenant/user；opaque 映射保存在进程字典，签名 key 由公开配置派生。来源预览固定使用本地用户，无法满足 OIDC 多用户隔离。

修复标准：Token/服务端记录绑定 tenant、user、run、evidence、expiry 和撤销状态；使用 `APP_SECRET_KEY` 或等价 SecretStr，不得由公开字段派生；映射持久化到 Redis/SQLite，并在预览时以当前认证主体和权威权限状态复核。增加跨用户、跨租户、过期、撤销和重启测试。

### 5. P1：OIDC/RBAC 尚未实现，配置 Gate 把 local_single_user 误判为 G3 可验收

代码中 `AUTH_MODE/OIDC_*` 只有配置定义，没有 JWT/OIDC 校验或请求主体。完整计划明确单用户身份仅用于开发，G3 前必须切回企业 IdP。

修复标准：实现认证端口、OIDC/JWT 验证、tenant/user/role/ACL 请求上下文和 401/403/404 语义；`AUTH_MODE=local_single_user` 时 G3 报告必须标记真实验收阻断，不得 `gate_ready=true`。缺少 IdP 配置时只报告键名，不输出值。

### 6. P1：G3 管理端不是计划指定的 Vue 3，且管理功能只是说明卡片

当前前端是原生 HTML/JS，`package.json` 没有 Vue/Vite；发布、回滚、权限、删除、治理与审计多数没有实际交互，不能满足“Vue 3 管理端/问答端”和 G3 管理端完成条件。

修复标准：建立 Vue 3 + Vite 工程，仍以 `npm run dev` 启动；实现问答、引用预览、反馈、发布/回滚、权限转换、删除/清理状态、审计和检索调试的可测试交互；不得使用 Docker或外部 CDN。

## 后续真实验收边界

上述代码问题关闭后，G3 仍需单独处理以下授权/依赖，不能用 Mock 报告替代：

- 真实 MySQL G3 六项 DDL 尚未获执行授权；
- 真实 LLM 合成文本探测尚未获计费授权；
- 企业 IdP 测试配置尚未提供，当前 `AUTH_MODE=local_single_user`；
- 真实云端撤权/删除/恢复演练尚未获授权。

先完成不需要新增外部权限的修复并重新提交审核；不得进入 G4。
