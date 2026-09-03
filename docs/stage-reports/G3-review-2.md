# G3 第二次审核结论

审核结果：`CHANGES_REQUIRED:G3_REVISION_2`

审核时间：2026-09-01T11:21:15+08:00

提交策略：`NO_COMMITS`。

## 已关闭的首轮问题

- ACL 同键同请求重试保持稳定，异请求返回 409；
- 删除后 document/version 为 404，SQLite 重启后 tombstone 仍存在；
- lifecycle、idempotency、cleanup outbox、audit 和引用记录已经持久化；
- 引用记录已经绑定 tenant/user/run/evidence/expiry/revocation；
- local principal、OIDC/JWT adapter contract、RBAC 和 Vue 3/Vite 工程已建立；
- 全量质量门通过：147 tests、Ruff 159 files、mypy 77 source files、OpenAPI 24 paths、SQLite v8、Vue build、npm audit 0、密钥扫描和 Docker 禁止项通过。

## 修订 2 仍需修复

### 1. P0：撤权/删除正文仍会先进入生成器，之后才被最终复核拦截

`TrustedQAService.ask()` 在调用 `generator.generate()` 后才执行 lifecycle-aware `permission.recheck()`。独立探针把已删除文档的旧 evidence 交给 provider：最终结果虽然是 `system_error`，但 tracking generator 已被调用 1 次并收到了正文。

这违反“未授权内容不得进入 LLM”的硬约束。最终输出复核不能替代模型调用前的权威复核。

修复标准：生成前对完整 context 做一次当前 tenant/user、ACL revision、lifecycle/tombstone、current version、有效期和 locator 权威复核；失败时 generator 调用次数必须为 0。生成后仍保留第二次完整复核，以覆盖生成期间撤权。

### 2. P0：删除正文仍会进入 Reranker，API 只在 Reranker 之后过滤

`HybridSearchService` 把 control-plane 返回的 `retrieval_text` 直接送入 Reranker；API 在 `search_service.search()` 返回后才用 lifecycle store 过滤 hits。

独立探针已复现：已删除文档最终 hits=0，但 tracking Reranker 收到了该文档正文。

修复标准：把权威 lifecycle/permission checker 注入检索服务或 control plane，在读取正文和调用 Reranker/父片段补全之前过滤；删除/撤权候选不得进入 Reranker，tracking Reranker 文档数必须为 0。不得仅在 API 输出层过滤。

### 3. P0：cleanup 可以在未清理任何数据时被标记为 COMPLETED

`POST .../cleanup/{target}:complete` 直接修改状态，没有调用或验证对应存储适配器。独立探针已复现：本地原件实际仍存在，API 却返回 `local_file=COMPLETED`。同样可以把未执行的 MySQL/Redis/Zilliz 清理标记为完成。

修复标准：只有 cleanup executor 成功执行并验证后置条件后才能完成；本地文件必须实际删除并确认不存在。未获授权的 MySQL/Redis/Zilliz/Zilliz Cloud 操作保持 `PENDING_APPROVAL`/`BLOCKED`，不能由管理 UI 人工伪造完成。增加失败、重试、部分完成、重启和后置条件不满足测试。

### 4. P0：SQLite 提交失败后内存状态可能开放权限，与磁盘事实分叉

生命周期对象先在内存修改为 ACTIVE/visible，再调用 `persist_state()`。独立故障探针令持久化失败：调用抛错，但当前进程 `memory_accessible=true`，重启后的磁盘状态仍 `accessible=false`。

修复标准：状态变更、audit、outbox 和 idempotency 必须通过真正的事务 Unit of Work 原子提交；失败时内存不得保留未提交状态，可使用 copy-on-write、DB-first 或失败后强制 reload。增加 publish/ACL/delete 的 SQLite 故障注入测试，权限开放路径必须 fail-closed。

### 5. P1：默认 `/ask` 仍未连接检索证据链

运行时只注册空的 `SyntheticEvidenceProvider()`；代码库中不存在基于 HybridSearchService/权威 control plane 的正式 EvidenceProvider。因此原生启动后的 `/ask` 固定走无证据路径，不能验证 search → evidence → generation 的本地集成。

修复标准：实现检索型 EvidenceProvider，使用与 `/search` 相同的 tenant/ACL/lifecycle/时态/代际/水位语义组装 EvidencePackage；确定性 generator 仍可使用 Mock，不调用真实 LLM。增加完整本地合成 E2E 和撤权并发测试。

### 6. P1：Vue 引用链接和进度流未完成

Vue 页面把后端返回的 `/api/v1/...` 相对引用直接作为 `href`，浏览器会访问 Vite 5173，而 `vite.config.js` 没有 API proxy；引用预览会落到错误服务。页面问答也调用普通 `/ask`，没有消费 `/ask:stream` 的 SSE 阶段进度。

修复标准：通过 `VITE_API_BASE_URL` 或 Vite proxy 生成正确引用地址；实现并测试 POST SSE 进度消费，verified 前不显示答案。移除可以直接“完成”外部 cleanup 的误导按钮，改为显示真实 outbox 状态/触发受控 worker。

## 配置与外部边界

- 用户已经确认暂时没有企业 IdP；`AUTH_MODE=local_single_user` 不阻止上述本地修复；
- `APP_SECRET_KEY` 仍未配置，真实引用跨重启继续是配置阻断；
- 不执行真实 MySQL G3 DDL、真实 LLM、ASR 或云端撤权/删除/恢复；
- 不得进入 G4。
