# UAT systematic v5 通用修复独立审核

审核结论：`CHANGES_REQUIRED:UAT_SYSTEMATIC_V5_GENERIC_REMEDIATION_R2_EDGE_CASES`

## r2 复审补充

r2 已将 future-only claim contract 接入独立 runner，且历史 artifact 仍隔离只读；但以下通用
边界仍未关闭：

1. `allow_cross_document=true` 的 response 可通过 runner 的 claim validator，却在
   `build_audit_manifest` 的二次默认验证中被 `UAT_CLAIM_CROSS_DOCUMENT_FORBIDDEN` 拒绝。
   这是可复现的通用 contract 不一致，而非单条用例问题。
2. future runner 未调用或持久化 `validate_audit_coverage` 的结果，不能为一个 future run
   生成、复验和恢复 1:1 case coverage 记录，T6 尚未完整接线。
3. 两个新增测试文件独立收集为 14 项，而 r2 报告写为 16 项；请使报告计数可复现，或在
   测试中补足并说明额外的两项。

需在 r3 中修正：将 cross-document policy 显式传入所有验证层，增加允许跨文档时的 fake
end-to-end 成功持久化和默认禁止时的失败测试；在首次运行和 resume 时生成/校验不可变、
content-free 的 coverage manifest，且不得留下“completed checkpoint 但无 coverage”的可接受
状态。继续禁止任何内容定向规则、历史 artifact 改写或外部调用。

已确认：12 个通用性质/变异测试通过，抗内容扫描为 0，历史 Reranker、LLM 与 combined Gate
哈希均未改变。但当前实现尚未关闭修复门：`real_uat.py`、LLM transport、UAT runner 和执行脚本
对 `uat_generic_remediation` 的引用均为 0，未来实际 UAT 仍会使用旧的
`status/answer/citation_ids` 自由文本契约与自报 locator。因此这些通用组件目前是未接线的库，
不能防止同类问题在新的提交中再次发生。

需完成的通用修正：

1. 新建仅面向未来提交的版本化 UAT contract/runner/plan 路径；不得修改、重跑或复写历史
   v1–v5 checkpoint、combined Gate 或 v4 result。
2. 在未来 evidence 入池点强制构建并验证 source integrity 与 evidence envelope；在未来 LLM
   请求中使用 claim contract，并在持久化前运行 claim/provenance/state validator 与派生 locator。
3. claim schema 必须按设计允许 `entity_id` 与 `field_key` 同时为 optional 并与 envelope 对齐；
   当前代码在 evidence 允许空值时又在 claim 中强制标识符，存在通用契约矛盾。
4. evidence envelope 重验必须完整保留并验证 source/render integrity 的 verified 标记与 hash，
   不能仅复算 source text 的两个 hash 而丢弃已提供的 rendered 证明。
5. 增加全链路 fake-transport 测试：通用生成的 evidence/ID/value 必须实际经过 future runner 的
   contract、validator、audit manifest 和 coverage；变异应在请求前或持久化前被拒绝。
6. 抗内容定向扫描须覆盖全部新增/修改的修复范围，且继续禁止当前问题、答案、candidate ID、
   姓名、日期、实体和事实字面量。

上述修复仅限本地与 fake transport；provider、Zilliz、Docker、模型重跑和提交均继续为 0。
