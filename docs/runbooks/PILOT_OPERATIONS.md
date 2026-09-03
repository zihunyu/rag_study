# 合成 Pilot 运维 Runbook

1. 创建 Pilot 与 feature flag，初始状态必须为 `DRAFT`、`simulated=true`。
2. 技术、安全、SRE 任一未签字或 VETO 时保持 `NO_GO`。
3. 最近 canary 必须 PASS，关联 UAT 全部 PASSED、逐 step 对齐且引用有效 evidence index。
4. 仅 `SIMULATED_GO` 可且只能生成一次 5%→25%→50%→100% 灰度批次；不得接真实流量。
5. synthetic canary 持久记录 seed/count/threshold/result；失败必须 NO_GO/rollback。
6. P0/P1 缺陷触发否决；回滚必须记录 trigger 并进入 `ROLLED_BACK`。
7. 所有写操作要求 Idempotency-Key；聚合更新要求 If-Match/CAS。
8. 任何页面或报告都必须显示 `real_acceptance=false`。
