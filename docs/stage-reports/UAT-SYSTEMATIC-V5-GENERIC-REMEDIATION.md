# UAT systematic v5 通用修复本地就绪审查

审查状态：`STAGE_REVIEW_REQUESTED:UAT_SYSTEMATIC_V5_GENERIC_REMEDIATION_READY`

## 改动范围

- 新增 `backend/src/ragkb/evaluation/uat_generic_remediation.py`：版本化的 source integrity Gate、evidence envelope、结构化 claim contract、claim/provenance validator、状态—正文状态机、派生 locator grounding、content-free audit manifest 与 coverage validator。
- 扩展 `backend/src/ragkb/infrastructure/uat_artifacts.py`：只新增不可变的 `uat-claim-audits/v1` 写入接口；拒绝 content、answer 和 question 字段，且不影响既有 result/checkpoint 路径。
- 新增 `backend/tests/test_uat_generic_remediation.py` 与 `scripts/check_uat_generic_remediation.py`；质量门接入后执行后安全的 v5 plan 检查和抗内容定向扫描。

## 各失效类型的关闭证据

| 类型 | 通用实现 | 回归证据 |
|---|---|---|
| T1 声明—证据绑定 | 每个 exact claim 校验 evidence ID、span hash、value substring、source version 和 locator hash | span/value/citation 变异被拒绝。 |
| T2 实体/字段/值绑定 | claim 必须与 evidence envelope 的 entity ID、field key 和 value 对齐 | entity/field/value 置换被拒绝。 |
| T3 跨文档边界 | envelope 带 source document ID/version；默认禁止多文档 answered claim | 两个不同 source document ID 的组合被拒绝，除非显式开放。 |
| T4 状态—正文 | `answered` 必须有 exact claims；其他状态禁止 claim，并由系统渲染状态正文 | 三种非 answered 状态的正文/claim 变异均被拒绝。 |
| T5 源完整性 | 拒绝控制字符、替换字符、空文本和 source/render 标准化不一致 | 注入控制字符及表示不一致均被拒绝。 |
| T6 审计覆盖 | audit manifest 绑定 case、question/bundle hash、证据 refs、claim hash 与 coverage | 缺失/错配 case、不可变写入变异均被拒绝。 |
| T7 可验证 locator | locator grounded 从已验证 claim/evidence 推导，而非调用方自报 | 无 claim 时为 false；每个 exact claim 的 locator hash 必须匹配。 |

## 非内容定向保证

- 专门扫描从审核包读取 78 个历史 ID、78 个答案和 17 个纠正引用，仅输出计数；三份新增/修改实现与测试源文件的历史文本匹配为 0，20-hex case ID 字面量为 0。
- 性质/变异测试使用重新生成的 ID、token、entity、field 和 value；未包含当前问题、答案、姓名、日期、实体或事实的规则、替换表或提示词例外。

## 本地质量证据

- 定向通用修复测试 12 passed。
- 全量 pytest 收集 316 条；Ruff 336 files；mypy 110 source files；frontend、OpenAPI、config、secret scan、v5 plan check 和抗内容定向扫描均通过。
- 完整质量摘要 41 项，`failed=0`、`skipped=0`；secret scan finding 0。
- 历史只读 artifact SHA 未变：Reranker v5 `188aded52174b60e399ea9d6448ffe383d5c553a050418581bfd20388e256317`；LLM v4 `7163f71791a974afac036e6eb8a6106f0de870275da47564b4d44359753cbb62`；combined-v5 Gate `ad5920fd2050797d4aaf3c952b68bffc515e9cc9adadb3a3dfd7f6eaf1f48d6b`。

本阶段 provider=0、Zilliz=0、Docker=0、模型重跑=0、commit=0。通用修复尚未授权接入新的 UAT 执行或修改任何历史结果；需先独立审核本地实现和回归策略。
