# Pilot 实现检查单

- [ ] technical/security/SRE 三方 synthetic signoff 已记录；
- [ ] 无 OPEN P0/P1 synthetic defect；
- [ ] canary 有固定 seed、成功/失败计数和 rollback trigger；
- [ ] 最近 canary PASS，关联 UAT 每一步与 expected 对齐且 evidence index 引用有效；
- [ ] 灰度仅生成 5/25/50/100 simulated batches，不接真实流量；
- [ ] UAT case/step/evidence/result/defect contract 完整；
- [ ] diagnostics/alerts 和原生进程 plan 可用；
- [ ] 所有状态明确 `simulated=true`、`real_acceptance=false`；
- [ ] 不得将 `SIMULATED_GO` 宣称为真实 Go。
- [ ] Idempotency-Key replay 稳定、异请求 409；If-Match/CAS 冲突 412；重复 rollout 不得 500。
