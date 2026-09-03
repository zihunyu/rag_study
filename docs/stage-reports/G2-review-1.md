# G2 第一次审核结论

审核结果：`BLOCKED_USER_INPUT:G2`

提交策略：`NO_COMMITS`。

## 已通过

- Python 3.12 全质量门：105 tests、Ruff、mypy、npm、OpenAPI、SQLite v3、无 Docker和密钥扫描通过。
- Zilliz 真实验证：29 fields、BM25、11 indexes、Loaded；4 条逐条 Strong-confirmed insert；BM25/Dense/RRF/ACL/时态/代际/watermark 通过；4 条清理且 remaining=0。
- Embedding 1/5、Reranker 2/5 真实合成探测通过，无自动重试。
- Redis 真实认证和 JSON set/get/delete 探测通过，测试键已清理。

## 当前阻断

- MySQL 连接使用配置数据库时返回错误码 `1049`（数据库不存在）。
- 不指定数据库时，同一凭据可成功认证并执行 `SELECT 1`；检测到 CREATE/ALL 等创建权限。
- 需要用户明确批准创建 `MYSQL_DATABASE` 指定的数据库，并执行已审核的 G2 MySQL migration plan；禁止输出数据库名、主机、账号或密码。

## 报告修正

`docs/stage-reports/G2.md` 必须把早期失败的“0 写入/0 检索”段落明确标为历史尝试，不能与最终 4/4 成功结果并列为“本次最终结果”；同时补充 Redis 真实通过和 MySQL 1049 阻断。

用户授权前停止 G2，不得进入 G3或执行任何提交。

