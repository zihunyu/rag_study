# 最终统一真实验证计划

只有所有代码实现完成后才执行以下真实套件：

1. 非 ASR 五类真实格式各 10 份及完整 metadata；
2. 真实模型质量、安全、成本和熔断；
3. MySQL G3/G4 migration 与回滚；
4. Zilliz/Redis/MySQL 对账、撤权、删除、清理和恢复；
5. 生产相似性能、容量、长稳和完整恢复；
6. 真实 UAT；

原始完整 G6 范围包含真实 7 天观察期；用户当前将其标记 `deferred_by_user`，不纳入
本轮最终统一验证 blocker。真实 UAT 与其他六类套件仍保留。

运行 `python scripts/generate_final_validation_plan.py` 只生成计划；它不得执行外部调用。
任何真实证据缺失时 acceptance report 必须为 `BLOCKED_REAL_EVIDENCE_MISSING`，synthetic
结果不能解锁。
