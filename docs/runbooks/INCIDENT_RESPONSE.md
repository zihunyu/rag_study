# 本地事故响应与观察期 Runbook

- 观察窗模型固定 7 天时长，但本地记录仅为 simulated 数据；使用 fake clock 测试。
- 只有实际时钟达到 ends_at、窗口 CLOSED、required metrics/coverage 完整且无采样缺口时
  才允许 `SIMULATED_COMPLETE`；RUNNING/空指标/未来时间一律 BLOCKED。
- P0/P1 incident 或 defect 阻断 observation readiness。
- 业务、技术、安全、运维四方签字缺一不可；VETO 优先。
- 结构化事件只记录 trace、类型、状态和计数，不记录正文或 Secret。
- 真实故障响应、生产值班和外部恢复统一留 `FINAL_UNIFIED_VALIDATION`。
- 最终报告即使 synthetic readiness 完整，也必须因真实证据缺失保持 `BLOCKED`。
- 真实 7 天观察已由用户设为 `deferred_by_user`，不得要求实际等待，也不纳入当前真实 blocker。
