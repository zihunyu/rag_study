# UAT systematic v5 失效类型独立审核

审核结论：`APPROVED:UAT_SYSTEMATIC_V5_GENERIC_REMEDIATION_LOCAL_ONLY`

- 审核包的 11 个不通过项与 4 个待修订项可归入 7 个通用类型：声明—证据绑定、
  实体/字段/值绑定、跨文档边界、状态—正文状态机、源文本完整性、审计覆盖与可验证 locator。
- 独立代码核对确认类型对应真实通用缺口：UAT bundle 只携带文本 evidence；LLM contract
  只要求 `status/answer/citation_ids`；runner 仅校验 citation ID 范围并把
  `locator_grounded` 写为真；result persistence 不保存可重放 provenance。
- 报告未包含当前 candidate ID，且明确排除了针对问题、答案、姓名、日期、实体或事实的
  专用规则、替换表、提示词例外和样本补写。拟议性质/变异测试重新生成 ID、token、实体和值。
- v5 Reranker、LLM v4 和 combined Gate 哈希保持不变；本阶段没有代码、样本、checkpoint 或
  结果改动，provider/Zilliz/Docker/commit 均为 0，secret scan=0。

现仅放行本地通用修复与非内容定向回归：证据信封/可追溯 manifest、结构化 claim contract 与
validator、状态—正文状态机、源完整性 Gate、派生 locator grounding 和审计导出。禁止修改当前
结果、历史 checkpoint 或样本，禁止模型重跑和任何外部调用。完成后必须提交
`STAGE_REVIEW_REQUESTED:UAT_SYSTEMATIC_V5_GENERIC_REMEDIATION_READY`，附完整质量门与
“无当前内容定向规则”的独立证据。
