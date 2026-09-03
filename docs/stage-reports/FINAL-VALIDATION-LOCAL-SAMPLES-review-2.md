# 最终统一验证本地样本阶段第二次审核

审核结果：`CHANGES_REQUIRED:SPREADSHEET_LOCATOR_COVERAGE`

审核时间：2026-09-01T16:53:33+08:00

## 已关闭

- DOCX 已正确标记为 Office conversion/page-mapping 阻断并从 eligible 排除；
- external plan 已分别列扫描/图片 10 份与 DOCX 10 份，未假定 provider；
- parsed/eligible/blocked chunks 和执行/质量状态已经分离；
- 每份样本增加处理后 SHA256、size、mtime 不可变校验；
- 0 外部调用、0 文件名/正文泄漏和 212 项全量质量门通过。

## 仍需修复

### P0：表格 13/13 是范围重叠假阳性

现有 9 个 spreadsheet `cell_range` 预期全部为跨多行范围，最大 12 行、15 列；Parser 输出逐行 range。当前 `_expected_locator_match()` 对每个预期只要找到任意一个实际 range 与之有矩形交集便算整项命中。因此预期 `A1:O12` 即使只有一行被解析也会被视为命中，不能证明完整定位覆盖。

修复标准：

- 对 expected cell range 按相同 sheet 聚合实际 locators；
- 要求 expected 矩形被实际 ranges 的并集完整覆盖，至少逐行验证列区间覆盖；仅 overlap 不得算通过；
- 或由 SpreadsheetParser 生成真实表级 span locator，并要求 span 包含 expected range；
- row 预期继续 one-based 精确匹配；
- 增加“只覆盖一行不得通过”“缺中间行不得通过”“列覆盖不足不得通过”“完整并集覆盖通过”的单元测试；
- 使用现有10份样本重跑。只有严格覆盖后达到13/13，259 chunks 才可 eligible；否则保持 BLOCKED_LOCATOR_CONTRACT。

在此修复通过前，Embedding 可靠基线仍为文本 PDF 25 + PPTX 385 = 410 eligible chunks、13 batches；不得采用 669/21。

继续本地离线修复，不需要用户提供新文件，禁止外部调用。
