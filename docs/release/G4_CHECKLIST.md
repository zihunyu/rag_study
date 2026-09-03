# G4 本地准备发布 / 回滚检查单

## 发布前

- [ ] 174+ 全量回归和 G4 新增测试通过；
- [ ] 当前五类非 ASR 真实样本 Gate 明确 BLOCKED 或有合法证据；原始范围为 6x10；
- [ ] audio 显示 `deferred_by_user`，不计入当前 ready，也不宣称支持；
- [ ] OCR/Office Stub 均标记 `real_acceptance=false`；
- [ ] publication candidate 为 VALIDATED/STAGED，generation/watermark/checksum 一致；
- [ ] 权限矩阵、Prompt injection、安全负向、撤权/删除/恢复探针通过；
- [ ] 性能报告包含规模、并发、Top-K、答案长度和环境，不宣称真实 SLO；
- [ ] 成本/熔断、迁移/对账均为 Dry-run；
- [ ] Git 保持 NO_COMMITS，本阶段不进入 G5。

## 回滚

- [ ] 旧 candidate 仍可恢复且在 rollback window；
- [ ] projection swap 与 document CAS 同事务；
- [ ] 失败时旧版继续 serving；
- [ ] tombstone 终态不可回退；
- [ ] 外部系统动作未获授权时停止并保持 plan-only。
