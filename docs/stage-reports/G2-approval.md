# G2 审核批准

结论：`APPROVED:G2_NO_COMMITS`

审核时间：2026-09-01T09:50:30+08:00

## 独立证据

- 配置报告：G2 `gate_ready=true`，0 个 G2 blocker；ASR 三项仅在 G4 阻断。
- MySQL 只读复核：连接与目标数据库选择成功；数据库字符集/排序规则为 utf8mb4；6/6 项目表完全匹配，全部 InnoDB、全部 utf8mb4；26 个索引；5 条迁移 ID 与 `mysql-control-plane:g2-v1` 完全匹配；回滚探针残留为 0。
- MySQL 创建/迁移证据：仅执行 1 条目标数据库创建语句，0 DROP、0 其他数据库修改；首次 5/5 applied，第二次 0 applied/5 skipped，幂等通过；输出不含数据库名、Host、用户名或密码。
- Redis 真实 set/get/delete 已通过且测试键已清理。
- Zilliz Cloud：29 fields、1 个 BM25 function、11 indexes、Loaded；4 条合成记录逐条 Strong-confirmed 写入；BM25、Dense、RRF、ACL、时态、代际和 watermark 验证通过；4 条均已删除，Strong remaining=0；没有 drop 或修改非项目资源。
- 模型探测：Embedding 1/5、Reranker 2/5，均通过，自动重试 0；未调用 LLM、ASR 或真实文档服务。
- 全量质量门：Python 3.12.13；pytest 107 passed；Ruff lint/format 131 files；mypy strict 63 source files；OpenAPI 14 paths（有 `/search`、无 `/ask`）；SQLite schema v3；npm、原生入口、Docker 禁止项与密钥扫描全部通过。
- `config` 目录仅有 `.env` 与 `.env.example`；`.env` 未被 Git 跟踪，也未输出配置值。
- Git HEAD 仍为 `b1f119b4d1259a129b0cce5dac412dbb5282cfbf`；本阶段没有 commit、merge、rebase、tag、push 或 PR。

## 审核结论

G2 的索引、独立检索、真实中间件与已批准云端/模型探测退出条件已满足。第一次审核中的 MySQL `1049` 阻断已经由用户授权后的定向数据库创建、迁移和独立复核关闭。

保持 `NO_COMMITS`。本审核先按开发任务当时的阶段交接约束停在 G2；用户随后于
2026-09-01T09:55:08+08:00 明确要求遵循原定自动推进规则，因此 G3 已另行开放。
