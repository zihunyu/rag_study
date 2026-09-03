# G4 本地验证 Runbook

1. 运行 `python scripts/check_env.py --gate G4 --allow-blocked`，只记录缺失键名。
2. 运行 `python scripts/check_format_samples.py --allow-blocked`；当前只计算五类 50 份，
   audio 必须显示 `deferred_by_user` 且不产生 blocker。
3. 运行 `python scripts/check_g4_validation.py`；报告必须为 `real_acceptance=false`。
4. 运行 `python scripts/run_quality.py`，确认无跳过、无真实外部调用、无 Docker。
5. 文档处理按 upload→Worker→quality report→human review→publication readiness 顺序执行。
6. 发布失败时保持旧 current version；删除/恢复时先重放 tombstone，再恢复可见内容。
7. 熔断器 OPEN 后只允许显式 HALF_OPEN 本地探针；成本计量 Dry-run 不发送请求。
8. `ASR_ENABLED=false` 时不得要求 ASR 三键或继续音频开发；未来启用时再恢复校验。
9. 真实样本或授权不足时停止对应真实动作，并引用 `G4-INPUTS.md`。

本 Runbook 仅用于生成的临时/合成数据，不得用于真实项目数据恢复。
