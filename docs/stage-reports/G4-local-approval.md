# G4 本地验证准备审核批准

结论：`APPROVED:G4_LOCAL_PREPARATION_NO_COMMITS`

审核时间：2026-09-01T13:14:04+08:00

## 独立证据

- Python `wave` 生成的合法 WAV 通过完整 upload API，complete=202；图片/音频 magic 正负回归覆盖 15 项；
- `BLOCKED_REAL_VALIDATION` 旧 Office Stub 在无 review 及 APPROVED review 后均 publish=409，保持 DRAFT；
- 缺 review、非批准 review、旧 quality revision 和 empty document 均按约定 fail-closed；
- 8 条合成安全用例输出 actual/expected/passed=8/8；hidden retrieval、cross-tenant text、forged citation、external egress 计数均为 0；
- 代表本地系统路径实际运行两个文档规模、两个并发和两个 Top-K，80 success/0 failure；结果仅为 smoke/测量 Harness，`slo_claimed=false`；
- 真实 Schema v11 临时 Runtime backup/restore 验证 queue、lifecycle、reference、RAG、publication、lineage、tombstone-first、cleanup outbox、幂等和文件哈希；`real_project_data_touched=false`；
- 成本/熔断保持 Dry-run，`billable_request_performed=false`；MySQL/Zilliz 仅 plan-only；
- 原始完整范围六类样本为 0/60；用户后续将 audio/ASR deferred，当前非 ASR 范围为
  五类 0/50，格式 Gate 保持 BLOCKED，没有伪造真实验收；
- 全量质量门：pytest 199 passed；Ruff 190 files；mypy 87 source files；SQLite v11/27 tables；OpenAPI 27 paths；前端行为测试和 Vue build；npm audit、密钥扫描、Docker 禁止项全部通过；
- Git HEAD 未变化，`config/.env` 未跟踪且没有输出配置值。

## 批准边界

本批准仅覆盖 G4 本地合成验证准备，不是 Release Candidate。Prompt injection 结果是确定性控制面安全契约，不代表真实模型抗越狱能力；本地性能结果是 smoke Harness，不代表生产 SLO。

## 用户范围重基线附录

- `APP_SECRET_KEY` 已配置，只验证 configured 状态；
- `ASR_ENABLED=false`，ASR 三键保持空白且不阻断当前 G4 非 ASR 范围；
- audio 历史契约、测试和未来恢复入口保留，但标记 `deferred_by_user`，不继续开发、
  不声明支持、不计入当前 ready；
- 企业 IdP 继续 deferred。

当前真实 G4 remaining blockers：

- 五类非 ASR 各 10 份、共 50 份合法真实难例及完整 metadata；
- 真实 LLM/MinerU/Embedding/Reranker 的调用授权和预算；
- MySQL G3/G4 DDL/迁移授权；
- Zilliz/Redis/MySQL 对账、撤权、删除、清理与恢复演练授权。

保持 `NO_COMMITS`，不得进入 G5。
