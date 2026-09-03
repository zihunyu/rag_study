# 最终统一验证：本地真实样本阶段

状态：`REVIEW_REQUESTED_LOCAL_SAMPLE_PHASE_REVISION_3`

范围：非 ASR 五类真实样本 50 份；audio 继续 `deferred_by_user`。本阶段仅在本机离线
只读处理，`real_acceptance=false`，未执行 MinerU、LLM、Embedding、Reranker、MySQL、
Zilliz 或 Redis 调用/写入。

## 隐私与安全边界

- 输入 Gate：五类各 10 份，共 50/50；脱敏、授权、rights、hash、metadata schema 均通过；
- 报告和 artifacts 不含文件名、正文、表格内容或敏感 metadata 值；
- 明细只使用不可逆匿名 sample ID、类别、计数、状态和错误码；
- 原文件 size/mtime 在处理前后保持一致，`source_samples_modified=false`；
- `external_call_count=0`、`network_call_performed=false`。

## 实际离线结果

| 类别 | 样本 | 状态 | Nodes/Chunks | Locator 命中 | 结构计数 | 总耗时（秒） | 单文件峰值内存（字节） |
| --- | ---: | --- | ---: | ---: | --- | ---: | ---: |
| 文本 PDF | 10 | SUCCESS 10 | 25 | 10/10 | 25 pages | 1.063 | 1,931,320 |
| 扫描 PDF / 图片 | 10 | BLOCKED_EXTERNAL_PARSER 10 | 0 eligible | 0/10 | 5 pages + 5 images | 0.048 | 1,997,688 |
| DOCX | 10 | BLOCKED_OFFICE_CONVERSION_OR_EXTERNAL_PARSER 10 | 370 parsed / 0 eligible | 0/20 | 235 paragraphs、40 tables、150 table rows | 0.345 | 2,839,485 |
| PPTX | 10 | SUCCESS 10 | 385 | 25/25 | 50 slides | 0.382 | 1,459,791 |
| XLS/XLSX/CSV | 10 | SUCCESS 10 | 259 eligible | 13/13 | 25 sheets、52 rows、72 columns | 0.183 | 1,396,783 |

执行状态与格式质量分离：`execution_passed=true`，50 份均完成安全/结构检查且 0 本地失败；
`format_quality_ready=false`。定位已验证、可进入后续 Embedding 的内容为文本 PDF、PPTX、
表格共 669 chunks。DOCX 虽本地结构解析出 370 chunks，但页定位 0/20，全部排除；扫描/
图片 10 份也排除。表格新增 row locator 与 sheet+cell-range 严格并集覆盖后达到 13/13。

表格 13/13 使用严格完整覆盖算法：同一 sheet 上，对 expected 矩形的每一行，目标列区间
必须被一个或多个实际 ranges 的并集连续覆盖；仅首行、缺中间行、列不足或 wrong sheet
均不通过。不是“任意 overlap 即命中”。

## 后续调用预算（只生成计划，不执行）

- DashScope `text-embedding-v4` 的 provider 上限为 batch size 10；locator-validated eligible
  为 669 chunks，对应 67 batches；当前用户配置仍为 32，必须改为非秘密值 10；
- 外部解析后的精确批次公式：
  `ceil((known_chunks + sum(external_parser_chunk_counts)) / batch_size)`；
- 对 20 份待外部处理文件使用安全上限时，总批次上限为 24,067；该值只是 capacity guard，
  不是预计成本、调用上限或预算承诺；
- MinerU 待处理：扫描 PDF / 图片 10 份；本阶段执行数为 0；
- Office conversion/page-mapping 待处理：DOCX 10 份；官方 MinerU v4 已明确支持，但真实
  DOCX 提交仍未授权，执行数为 0；
- Reranker/LLM 尚缺真实评测问题、预期答案和证据引用，执行数为 0。

详细匿名结果位于 ignored artifact：
`artifacts/final-validation/local-samples/details.json`。后续零调用计划位于：
`artifacts/final-validation/external-call-plan.json`。

## 真实 UAT 输入

下一真实阶段仍需用户一次性提供或确认：

1. 真实 UAT 问题；
2. 每题预期答案或业务判定标准；
3. 预期证据/定位；

或者授权系统根据已授权样本生成候选问题，再由用户在任何模型调用前完成复核。未经选择
和复核，不执行真实 LLM/Reranker 调用。

## 质量证据

| 检查 | 结果 |
| --- | --- |
| 五类真实样本准入 | PASS；50/50，0 blocker；audio deferred |
| 本地样本执行 | PASS；execution_passed=true、0 failed、源文件 SHA/size/mtime 不变 |
| 格式质量 Gate | BLOCKED；format_quality_ready=false；DOCX10 + 扫描/图片10 待外部处理 |
| pytest | PASS；214 tests，0 failed，0 skipped |
| Ruff lint / format | PASS；221 files |
| mypy strict | PASS；93 source files |
| OpenAPI / SQLite | PASS；51 paths / Schema v14 |
| final local sample quality step | PASS；0 external calls，0 filenames/content emitted |
| Vue / secret scan / Docker policy | PASS |

STAGE_REVIEW_REQUESTED:FINAL_VALIDATION_LOCAL_SAMPLES（修订 3）
