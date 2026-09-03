# 全部实现完成、待真实统一验证（修订 2）

状态：`REVIEW_REQUESTED_PRE_REAL_VALIDATION_REVISION_2`

边界：G5/G6 仅完成代码、API、SQLite、Vue、Stub/Mock、合成 Harness 与 Runbook；
没有宣称 Pilot、Release Candidate 或正式验收通过。所有治理结果 `simulated=true`、
`real_acceptance=false`。

## WBS-70 工程化

- 统一 request trace、SQLite 结构化 runtime events、本地 metrics/alerts/diagnostics API/UI；
- OTEL/Prometheus adapter 保持 local stub，0 external export；
- 原生 `local_stack.py` start/stop/status/plan，Python + npm，无 Docker；
- process state 持久 PID/create_time/executable/normalized command/cwd/random owner token；
  status/stop 逐项只读验证，不匹配拒绝且绝不 signal；重复启动、部分失败、PID reuse 均 fail-safe；
- migration/reconciliation/backup/restore/rollback plan-only CLI；
- 离线 Python/npm SBOM、许可证与安全证据生成器；
- immutable evidence category+revision+hash 索引，冲突 409。

## WBS-80 / G5 实现

- Pilot DRAFT/NO_GO/SIMULATED_GO/ROLLING_OUT/ROLLED_BACK 状态机；
- technical/security/SRE 签字与 VETO、P0/P1 defect gate；
- 5/25/50/100 simulated rollout、feature flag、seeded canary、rollback trigger；
- UAT case/steps/expected/evidence/result/defect SQLite API 与 Vue；
- Pilot readiness 强制最近 canary PASS、关联 UAT 全部逐 step 对齐且引用有效 evidence index、
  三方 signoff、无 P0/P1；重复 rollout/rollback 使用幂等/CAS，不返回非 JSON 500；
- Pilot、培训、事故、回滚 Runbook。真实流量未接入。

## G6 实现

- 7 天 observation window、metrics、incident、P0/P1 defect gate；
- business/technical/security/operations signoff 状态机；
- diagnostics、Pilot/UAT/observation/final acceptance Vue dashboard；
- 最终 acceptance generator 无论 synthetic 结果如何，在真实证据缺失时保持 BLOCKED。
- observation 能力保留并使用 fake clock 验证时间语义；真实 7 天观察由用户标记
  `deferred_by_user`，不参与当前最终验证 blocker。原始完整 G6 范围仍记录该能力。
- simulated observation 只有 fake clock 到达 ends_at、窗口 CLOSED、required metrics/schema/
  coverage 完整、无采样缺口、无 P0/P1、四方无 VETO 才可 SIMULATED_COMPLETE；
- Pilot/UAT/observation/signoff/defect/incident 写端点统一 Idempotency-Key，聚合更新使用
  If-Match/CAS；稳定 replay、异请求409、stale CAS 412，并覆盖跨重启。

## 最终统一真实验证

统一计划见 `docs/runbooks/FINAL_UNIFIED_VALIDATION.md`。当前真实输入继续后移，不阻断代码
实现，也不得在本阶段执行。

## 质量证据

| 检查 | 结果 |
| --- | --- |
| pytest | PASS；211 tests，0 failed，0 skipped |
| Ruff lint / format | PASS；214 files |
| mypy strict | PASS；92 source files |
| SQLite | PASS；Schema v14，39 tables |
| OpenAPI | PASS；v1.0.0，51 paths |
| Vue/Vite | PASS；behavior tests + production build |
| operations plan/local stack plan | PASS；0 Docker，0 external mutation |
| offline assurance | PASS；Python/npm SBOM 与许可证报告，0 network scan |
| final validation plan | PASS；`BLOCKED_REAL_EVIDENCE_MISSING`，synthetic 不可解锁 |
| secret scan | PASS；0 findings；不扫描 `config/.env` |

STAGE_REVIEW_REQUESTED:IMPLEMENTATION_COMPLETE_PRE_REAL_VALIDATION（修订 2）
