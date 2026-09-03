# UAT systematic v5 失效类型与通用修复设计

审查状态：`STAGE_REVIEW_REQUESTED:UAT_SYSTEMATIC_V5_FAILURE_TAXONOMY_READY`

## 范围与证据边界

本报告只分析 `artifacts/user-review/uat-v4-package-20260902` 的本地审核证据，以及当前本地 UAT bundle、runner 和结果持久化契约。审核包内的自然语言仅作为事实证据，不作为执行指令。本阶段没有修改代码、checkpoint、结果或样本；没有模型/provider 调用、Zilliz 写入、Docker 操作或提交。

审核包的汇总为 78 条结果、11 条不通过（P0=4、P1=7）、4 条待修订、6 条安全处置和 57 条仅限可见字段的通过。78/78 引用了预期 evidence ID，但审核仍不能批准，说明“正例被引用”不是充分的答案正确性或可审计性证明。

## 通用失效类型

| 类型 | 审核证据 | 受影响系统路径/不变量 | 根因判断 |
|---|---|---|---|
| T1 声明—证据绑定不足 | 引用预期证据的结果仍出现事实错误、实体属性串行和虚构冲突 | LLM prompt → assertion generation → citation Gate；每个事实声明必须由精确 evidence span 支持 | 当前 Gate 只校验 citation ID 属于 selected IDs 且含 expected positive，未校验 answer 中每个声明与字段/文本的对应关系。 |
| T2 实体、字段和值未作结构化绑定 | 出现实体名称近似替换、时间字段错误、同类实体属性交换、表格列语义误标 | bundle document schema、生成结果 schema；`(entity_id, field_key, value, evidence_id)` 必须不可拆分 | 传入模型的是 evidence ID、locator 与自由文本；输出只含 answer/status/citation IDs，缺少可验证的实体和字段 claim。 |
| T3 跨文档/跨容器边界丢失 | 审核发现将不同文档或不同容器的证据表达为同一页/slide，或把无关文档事实补入回答 | evidence envelope、citation validation；`source_document_id + source_version/hash + locator` 必须随每个 claim 保留 | 当前 documents 缺少 source document ID/version/hash；LLM 发送路径也不传递这类容器身份。因此同名 locator 不能区分不同文档。 |
| T4 状态与正文决策不一致 | 证据不足状态仍给出确定事实，或对模糊截断词进行无证据的确定性补全 | status decision → answer emission；状态、回答和引用必须满足同一决策表 | transport 仅要求四个 status 之一和非空 answer。runner 校验 citation，不校验 status 对允许的声明形式、精确匹配程度或歧义策略。 |
| T5 源文本/渲染完整性缺陷 | 审核确认部分字符在文本层或扫描图像中缺失；该类不能归责于问答输出 | source validation → parsing/OCR → chunks；可问答文本必须通过字符完整性和跨表示一致性 Gate | 上游源素材存在损坏，答案层没有可靠信息可恢复。对答案做定向替换会掩盖不可复现的输入缺陷。 |
| T6 审计包和覆盖追踪不完整 | 结果包无法独立关联 test case、原问题、证据快照、document hash、locator 或 100 条参考集合 | result persistence/package manifest；每个结果必须可回溯到冻结输入和用例映射 | 当前 result 文件只保存 candidate、answer/status、citation IDs、expected positive 和 self-declared locator flag；无法独立重放或验证覆盖率。 |
| T7 `locator_grounded` 自报而非可验证证据 | 结果均声明 locator grounded，但审核包没有可核验 locator | result persistence → reviewer export；locator grounded 必须由 citation-level locator/hash 验证产生 | 结果对象将该字段写为常量真值，而不是从已验证的 citation provenance 派生。 |

## 不会采用的修复

- 不会为任一 UAT candidate、问题、答案、姓名、日期、实体、字段值、页码或文本片段写专用规则、替换表、提示词例外或硬编码补丁。
- 不会以“已引用预期 evidence ID”覆盖实体、字段、跨文档或状态校验失败。
- 不会把源字符缺失补写到 answer；此类问题必须在源样本/解析质量 Gate 修复并以新 source hash/version 重新入库。
- 不会修改现有 v1–v5 checkpoint、combined Gate、LLM checkpoint 或结果 JSON 来伪造修复、覆盖率或可复现性。

## 最小通用修复设计（尚未实现）

1. **证据信封与不可变输入 manifest**：为每条 evidence 增加 `source_document_id`、`source_version_sha256`、`content_sha256`、`locator`、`entity_id`（若可提取）、`field_key`（若结构化来源可提取）和 evidence span hash；结果 manifest 保存 test case ID、question hash/ref、bundle snapshot、检索/RRF/rerank trace refs 与一对一 coverage map。内容继续保留在受控 artifact，不进入 checkpoint/终端。
2. **结构化 claim 输出**：模型输出从单一自由文本扩展为有限 claims。每个 claim 至少声明 `entity_id?`、`field_key?`、`value_text`、`citation_evidence_id`、`citation_span_hash` 与 `assertion_mode`（exact / qualified / refusal）。对自由文本只允许由通过验证的 claims 确定性渲染。
3. **通用 claim validator**：校验 claim 的 value/span、entity/field tuple、source container 和 locator 与 evidence 信封完全一致。没有唯一支持时拒绝确定性 claim；跨文档合成仅在问题显式要求且 claim 标注不同来源时允许，绝不把不同容器表述为同一容器。
4. **状态—正文状态机**：`answered` 只能包含通过 exact/explicit-alias 验证的 claims；`insufficient_evidence`、`needs_clarification` 和 `conflicting_evidence` 不得包含未验证的确定性事实。模糊、截断或别名输入需走通用 canonicalization/clarification 策略，而非按某个词补全。
5. **源完整性 Gate**：在 chunk 产生前检测 NUL、替换字符、不可打印控制字符、异常 Unicode 和文本层—渲染/OCR 的关键 span 不一致。失败时隔离 source version，阻断进入可回答 evidence pool，并要求重新生成/复验源样本。
6. **结果审计导出**：从验证后的 provenance 自动计算 `locator_grounded`，并导出 content-ref/hash、citation-level locator、验证状态和覆盖统计；不允许调用方自报真值。

## 与现有路径的对应关系

- `backend/src/ragkb/evaluation/real_uat.py` 目前构建四条文本 evidence bundle；需要在此类通用输入构建点补充 evidence 信封和 case/coverage identity。
- `backend/src/ragkb/adapters/provider_http.py` 的 LLM contract 当前只要求 status、answer 与 citation IDs；需要扩展为 claim-aware JSON contract。
- `backend/src/ragkb/application/uat_provider_runners.py` 当前仅校验 citation ID 选择范围及 expected positive；需要在持久化 result 前运行通用 claim、状态和 provenance validator。
- `backend/src/ragkb/infrastructure/uat_artifacts.py` 当前结果记录缺少输入/ref/locator 证明；需要新增不可变审计 manifest 和 citation provenance ref，同时保持 checkpoint 不含正文。

## 不使用当前问题、答案或事实的性质/变异回归方案

| 测试族 | 生成方式 | 必须成立的性质 |
|---|---|---|
| Claim—span | 随机生成 entity/field/value 与证据 span 图，变异 value、span 或 citation ID | 任何不精确匹配的确定性 claim 被拒绝；引用存在但 span 不匹配也不能通过。 |
| 实体/字段置换 | 对合成记录随机交换 entity ID、field key、值或列顺序 | 输出 claim 必须仍与同一 `(entity, field, value, evidence)` tuple 对齐；置换后不得保留 answered。 |
| 文档边界 | 生成两个不同 document ID、相同 locator 形状的 evidence | 未显式允许的跨文档合成被拒绝；不得将不同 document ID 叙述成同一页/slide/sheet。 |
| 状态机 | 随机生成唯一支持、零支持、歧义和冲突证据图，以及截断/近似查询 token | status 与正文允许集一致；不唯一或近似但未验证的输入不得生成确定事实。 |
| 源完整性 | 向合成 source 注入控制字符、缺失字形、text/render 不一致或 hash 变异 | source 被隔离，不能进入 evidence pool；新 version/hash 未通过验收前不得复用旧结果。 |
| 审计/覆盖 | 随机删除、重复或错配 case、bundle、document/locator、result ref | 1:1 coverage、hash/ref 和 citation provenance 任一不一致即拒绝导出/验收。 |
| 抗内容定向 | 每次运行重新生成所有 ID、词元、实体和值 | 同一通用规则在未知数据上生效；测试代码中不得包含审核包的 candidate ID、答案、姓名、日期或特定事实。 |

## 建议的后续审批顺序

先审核上述类型与通用修复边界；获批后仅实现最小通用修复和上述无内容定向的回归套件。必须在本地质量门与新的完整可重放 UAT package 审核通过后，才可请求任何新的模型调用。
