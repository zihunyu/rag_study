# 最终统一验证本地样本阶段第一次审核

审核结果：`CHANGES_REQUIRED:FINAL_VALIDATION_LOCAL_SAMPLES`

审核时间：2026-09-01T16:36:58+08:00

提交策略：`NO_COMMITS`。

## 已通过

- 五类真实样本准入 50/50，文件、SHA256、脱敏、授权、rights、classification 和 metadata schema 通过；
- 本阶段只在本机离线读取，0 网络、0 MinerU/LLM/Embedding/Reranker、0 MySQL/Zilliz/Redis 写入；
- 报告和 artifacts 未包含文件名、正文、表格内容或敏感 metadata 值；
- 20 份文本 PDF/PPTX 的 locator 全部命中；10 份扫描/图片明确外部阻断；DOCX/表格定位缺口如实报告；
- 全量质量门通过：212 tests、Ruff 219 files、mypy 93 source files、OpenAPI 51 paths、SQLite v14、Vue、密钥扫描和 Docker 禁止项。

## 必须修复

### 1. P0：外部解析计划遗漏 10 份 DOCX

DOCX 真实样本 expected locator 为 page，共 20 项；原生 python-docx 路径只生成 char_range，实际命中 0/20，并明确携带 `page_mapping_unavailable_in_native_docx_route`。这已触发计划中的 Office→PDF/外部解析降级条件。

当前 external call plan 却只列扫描/图片 10 份，仍把 DOCX 370 chunks 当作可用已知 chunks。该预算会在页级引用未解决前错误地进入 Embedding。

修复标准：DOCX 10 份标记 `BLOCKED_OFFICE_CONVERSION_OR_EXTERNAL_PARSER`，从可 Embedding 集排除；外部计划分别列扫描/图片 10 份和 DOCX conversion/page-mapping 10 份。未确认 MinerU 是否直接支持 DOCX 前，不得把两者合并为已批准调用。

### 2. P0：表格 locator 0/13 仍被计入可 Embedding chunks

表格 expected locator 中 9 项为 sheet+cell_range、4 项为 row；当前 SourceLocator 没有 row 字段，且逐行 cell range 与 expected 范围的精确匹配为 0/13。259 chunks 在引用定位未通过前不能进入检索发布预算。

修复标准：在不修改用户 metadata 的前提下，统一表格 locator 契约：支持 row，明确 cell-range 包含/重叠语义或生成表级 span locator；逐项重新对账。只有 locator 满足预期的 chunks 才计为 eligible；未解决样本必须 BLOCKED_LOCATOR_CONTRACT，不能 SUCCESS_DEGRADED 后继续 Embedding。

### 3. P0：Embedding 预算包含未通过定位的 629 chunks

当前 known_chunks=1039、known_batches=33；其中 DOCX 370 和表格 259 均定位 0 命中。当前已确认可进入后续阶段的只有文本 PDF 25 + PPTX 385 = 410 chunks，即在 batch_size=32 时暂为 13 batches。

修复标准：预算区分 `parsed_chunks`、`locator_validated_eligible_chunks` 和 `blocked_chunks`；Embedding 只使用 eligible。外部解析或 locator 修复后再重算，不得以 safe upper bound 作为成本预测或执行上限。

### 4. P1：源文件未修改只比较 size/mtime

验证器处理前后只比较 `(size, mtime_ns)`，报告却声明 source_samples_modified=false。同大小写回并恢复时间戳无法被发现。

修复标准：使用已授权 metadata SHA256 作为处理前基线，处理后重新计算 SHA256；同时保留 size/mtime。任一不一致立刻 FAILED/SAMPLE_MUTATED，且停止该样本后续处理。

### 5. P1：质量门把“执行成功”标成 `final_local_samples PASS`

本地步骤执行成功，但 10 external blocked、20 locator degraded，格式质量并未 ready。统一 quality 输出 `PASS` 容易被误解为真实格式通过。

修复标准：分别输出 `execution_passed=true`、`format_quality_ready=false`、明确 blockers；脚本退出码可表示执行成功，但 Gate/report 不得使用无边界的 PASS。只有五类定位/解析条件满足后才允许 format_quality_ready=true。

## 边界

这些修复继续使用现有已授权样本、本地代码与匿名聚合，不需要用户再提供文件，也不得执行任何外部或计费调用。
