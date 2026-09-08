# 登录与知识库权限

RAGSPACE 支持 `AUTH_MODE=password`。全局身份只有“总管理员”和“普通账号”；普通账号在每个知识库分别获得“知识库管理员”或“仅问答”。管理 A 库不会自动获得 B 库的管理权限。

## 使用方式

- 总管理员：登录后进入知识库首页，在“用户管理”创建账号、重置密码、停用账号和逐库分配权限。创建账号不自动分配任何知识库。
- 知识库管理员：默认进入负责的库，可以处理、复核、发布和撤回文档，修改图表识别结果；在“成员”添加、移除已有普通账号的问答权限。不能创建账号或任命管理员。
- 普通用户：只有知识问答与可问答知识库入口。引用面板提供实际引用片段及相关原图，不提供文档列表、完整文档或下载入口。
- 同一账号可以管理 A、仅问答 B、无法访问 C。后端按实际资源所属知识库校验，修改地址或资源 ID 不能扩大权限。
- 无分配时显示“尚未分配知识库，请联系管理员”。知识库回收站只对该库管理员可见；恢复库不会重新发布先前单独撤回或删除的文档。

## 初始化与部署

安装项目依赖后，从项目根目录运行：

```powershell
.venv/Scripts/python.exe scripts/bootstrap_admin.py --username admin --activate
```

命令先追加账号表，再交互输入两遍密码，沿用 `AUTH_LOCAL_USER_ID=local-admin`。不接受命令行密码或环境变量密码。`--activate` 只修改 `config/.env` 的 `AUTH_MODE`；之后重启后端和处理服务。`--check` 只报告初始化状态，`--migrate-only` 仅追加账号表。重复初始化不会覆盖已有账号或密码。

已有独立初始化的账号需要启用时，将 `config/.env` 中的 `AUTH_MODE` 设为 `password` 后重启。不要删除业务库、重建向量索引或修改旧迁移编号。密码模式下认证失败不会回退为本地管理员。原 `local_single_user` 和后端 OIDC 模式保留为显式配置；本次登录页使用账号密码，不提供 OIDC 登录按钮。

默认密码长度 15–128 字符。使用 Argon2id（19 MiB、2 次迭代、并行度 1），数据库仅保存不可逆哈希。总管理员创建、重置账号后，临时密码随机生成、只返回一次、24 小时有效，首次登录必须改密。总管理员修改自己的密码使用右上角“修改密码”。禁止停用或降级最后一个已完成正式密码设置的有效总管理员。

Cookie 为 `ragkb_session`，HttpOnly、SameSite=Lax。服务端只保存会话令牌摘要；空闲 30 分钟、最长 12 小时。身份定时检查不会延长空闲期限。停用、重置、改密和退出撤销会话。前端不把会话令牌放到 localStorage。

仅回环地址允许 HTTP。对外部署必须使用 HTTPS，设置 `AUTH_COOKIE_SECURE=true`，并将 `CORS_ORIGINS` 设置为实际前端来源。反向代理应正确传递 HTTPS 协议，限制代理信任范围；不要开放调试服务器。所有修改请求（包括登录、上传和 SSE 提交）都需要可信 Origin 与 `X-CSRF-Token`。先访问 `/api/auth/csrf` 获取匿名会话和 CSRF；登录成功会轮换 Cookie 与 CSRF。

Redis 分别限制账号与来源 IP 的登录尝试。限流返回 429 和 Retry-After；限流服务异常返回 503，不跳过校验。账号不存在、密码错误、停用与临时密码过期使用统一提示。

## 接口与生效边界

密码模式契约见 [password-openapi.json](openapi/password-openapi.json)，兼容模式契约见 [openapi.json](openapi/openapi.json)。可以使用 `scripts/export_openapi.py --auth-mode password --check` 检查快照。

| 接口 | 用途 |
|---|---|
| `/api/auth/csrf`、`login`、`logout`、`me`、`password` | 登录和本人信息 |
| `/api/admin/users`、`/{id}:reset-password` | 账号管理 |
| `/api/admin/users/{id}/spaces` | 按用户查看和修改各库权限 |
| `/api/spaces/{id}/members`、`member-candidates` | 本库成员及最小身份搜索 |
| `/api/spaces/{id}:delete`、`:restore` | 可恢复软删除 |
| `/api/spaces/{id}/audit-events`、`usage` | 本库操作记录和实际用量 |
| `/api/admin/audit-events` | 账号与权限审计 |
| `/api/admin/lifecycle-audit-events` | 保留原生命周期审计接口 |
| `/api/admin/conversation-audits`、`/{id}:read` | 独立只读问答审计；阅读必须填写原因 |

成员变更使用 If-Match。两种分配入口都调用同一个授权服务。用户分配面板逐库提交，逐项展示已保存或失败结果；多个库不是一个全局事务，失败时刷新后继续。库软删除、恢复同时使用 If-Match 与 Idempotency-Key。

账号和成员状态每次请求重新读取；会话列表和统计先限制授权范围，再分页或汇总。检索、答案最终放行、会话正文和引用再次检查当前权限。缓存同时隔离用户与权限修订。MySQL 使用租户级 GET_LOCK，SQLite 使用数据库文件旁的进程间锁；撤权和 ASGI 响应正文放行使用同一把锁。模型调用不占用此锁。撤权先完成时，尚未放行的正文与引用不会发送；已经发送到客户端的内容无法追溯收回。

前端退出、换账号或检测到权限修订时，会终止旧请求、清空会话和知识库状态、释放来源图片 Blob。页面获得焦点及每 30 秒刷新身份。断网时服务端继续执行授权检查；浏览器已显示的内容不具备远程抹除能力。

图片、文档的生命周期、ACL、有效版本和证据复核检查仍然生效。知识库管理员不能让未复核资料绕过 RAG 验证。普通用户的覆盖检查报告仅保留计数，不返回未引用文档、章节清单和管理信息。

问答审计索引不包含标题和问题正文。总管理员填写原因后，由独立接口读取历史快照并记录查看者、所有者、知识库、时间、原因；不会冒充用户或改变其会话。

## 数据与运行记录

新增 `auth_users`、`auth_sessions`、`space_memberships`、`space_access_state`、`account_audit_events`、`account_commands` 及查询索引。数据库迁移为追加建表和索引，不修改业务文档、发布状态和问答归属。旧单轮 RAG 记录保持原样，不推测会话归属。

本库用量来自真实调用记录。图片用量按文档版本汇总，问答链路调用从本次升级后按知识库记录；旧调用不猜测归属。未配置价格的调用显示为“未定价”，不计为免费。关闭 `MODEL_USAGE_ENABLED` 时，问答用量显示“未启用”。全系统历史用量仍由总管理员在系统状态查看。

## 验证

```powershell
.venv/Scripts/python.exe -m pytest backend/tests/test_password_accounts.py -q
.venv/Scripts/python.exe scripts/check_account_databases.py
```

第二个命令使用唯一临时 MySQL 数据库，验证完只删除该临时库；不会操作业务库。它验证 SQLite/MySQL 相同行为、迁移幂等和 MySQL 跨进程权限锁。

浏览器验收使用 `scripts/auth_fixture_server.py` 创建临时 SQLite 运行于 8002，再在 frontend 目录运行 `node scripts/accounts-acceptance.mjs`。脚本使用三个独立浏览器上下文和 390、1024、1440px 视口。固定测试密码仅用于独立测试库，不能用于部署。结果保存在 `artifacts/reviews/20260908-account-access/`。

当前前端的 Mermaid 依赖仍有较大的懒加载分包；不影响登录页与权限校验。多机部署仍受原项目的本地文件存储限制，不因本次加入数据库权限锁而变成多机共享存储架构。
