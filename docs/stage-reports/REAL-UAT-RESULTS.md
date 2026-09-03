# 真实 UAT 模型执行结果

状态：`BLOCKED_RERANKER_POSITIVE_NOT_IN_TOP_K_LLM_NOT_STARTED`

本次严格按批准的两阶段顺序执行。预检确认 approved candidates 78/78、bundle hash 78/78、
出口策略与所需配置通过，Reranker/LLM checkpoint 和结果目录均为空。预检及结果核对没有
输出问题、证据正文、配置值、原文件名或供应商响应正文。

## Reranker

- 只执行一次，批准上限 78 requests，automatic retries 0；
- 实际 requests 2、records 2、completed 1、failed 1、UNKNOWN 0；
- positive top-2 Gate passed 1；第 2 条未进入 top-2，安全错误码
  `UAT_RERANKER_POSITIVE_NOT_IN_TOP_K`；
- 失败后立即停止，没有第二次 Reranker 执行；
- checkpoint SHA-256：`5e2f739a4136afa827ade769b0b4fe1f6715ba278ee2efe7f05457224c5d4df7`；
- checkpoint 禁止字段命中 0，不含 question/document/content/answer、URL、API key、
  endpoint、provider message/body；配置秘密值与 Endpoint 值命中均为 false。

## 本地人工复核证据

- artifact ref：`uat-result-review/reranker-failure-1.json`；
- artifact SHA-256：`8a330b506ce9b3d4d03ce99c5cf1420c06d346524c0e1ce6bda8f50c8d9eafd3`；
- documents 4；正文只存在该本地受控 artifact，终端与报告未输出；
- 旧 v1 runner 没有在业务 Gate 失败前持久化完整供应商排序，因此
  `provider_order_unavailable=true`、`positive_rank_unknown=true`；没有伪造排序；
- 生成前后 v1 checkpoint SHA-256 一致。

## LLM 与结果

- Reranker 未达到 completed 78/78、Gate 78/78，因此 LLM 未启动；
- LLM requests 0、completed 0，checkpoint 不存在；
- 本地 UAT result count 0，citation Gate 尚未执行；
- `real_uat_passed=false`，不得声明真实 UAT 通过。

## Candidate 2 本地改写建议

- 使用确定性本地算法从 positive 与 3 个 distractors 提取区分性 CJK n-gram / ASCII
  token，并过滤过短项、纯数字噪声和敏感键/赋值模式；
- 生成 3 条 proposal；动态内容仅由原 question 与 positive 中实际出现的词组成，
  `evidence_external_facts_added=false`；Reranker/LLM/网络调用均为 0；
- artifact ref：`uat-result-review/candidate2-revision-proposals.json`；
- artifact SHA-256：`f281ace99a60efa8ba64c0ead0002e1f9f052993da1126e4b9d3e9c19a2952e7`；
- artifact 包含原 candidate ID、原 question、3 条 proposals、每条 local term hashes、方法
  revision 与 `PENDING_USER_REVIEW`；真实文本只在本地文件；
- approved/pending、v1 checkpoint 与全部 78 个 bundles 共 81 个输入生成前后 hash 一致；
  v2 checkpoint 未创建。
- proposal 补强后完整质量门：288 tests、Ruff 288 files、mypy 106 source files、frontend、
  OpenAPI、配置检查、两项本地复核 artifact 复验与 secret scan 全部通过。

## Candidate 2 Reranker v2 单条诊断结果

- proposal 1 的独立 revision/bundle 通过预检后只执行一次；
- request 1、completed 1、Gate passed true、positive rank 1、response index count 4；
- automatic retries 0、第二次执行 false、LLM request 0；
- v2 checkpoint SHA-256
  `7fccd3f4aa9eff6fbe0128753bdf9e51b01cb0da932248838044542b9e031eb1`；
- checkpoint 已保存 4 个匿名 ranked evidence IDs、positive rank、response count 与 Gate；
  不含 question/content、URL、API key、Endpoint 或 provider message/body；
- proposal、failure review、v1 checkpoint、revision 与 diagnostic bundle hash 全部不变；
- 这是单条受控诊断通过，不代表其余 76 条 Reranker 已完成，也不代表真实 UAT PASSED。

## Reranker continuation v3 结果

- 唯一执行 request 2、completed 1、failed 1、UNKNOWN 0、automatic retries 0；
- 第 1 条 remaining candidate 通过；下一条 positive rank 4/4，错误码
  `UAT_RERANKER_V3_POSITIVE_NOT_IN_TOP_K`；
- 失败 checkpoint 保存完整 4 项 ranked evidence IDs、positive rank 与 response count；
- checkpoint SHA-256：`72a2fdf766891a8414bc6a77848828d41d3bf96274fcb82094b55f209ce4b30e`；
- 未第二次执行，combined Gate 未生成，LLM request 0、results-v2 0；
- 当前已有 3 条 Gate PASS，原 candidates 4–78 共 75 条未通过或未执行。

## Systematic revision v4

- 对原 candidates 4–78 一次性生成 75/75 条 positive-only 推荐问题；任一失败时会整套拒绝，
  本次完整生成后才原子落盘；
- review ref：`uat-systematic-revision-v4/approved-review.json`，SHA-256
  `b3be4dd16601548ee27dc9551461f5fe87759f3721383595cb5abdc16e42d670`；
- manifest SHA-256：`30b996d1f0f7ab9b5e5dd2b0bb6ce23c2845a1cdca5f4434a5fa1f064f4b56af`；
- 75 个 revised bundles 的 evidence documents/IDs/locators/content hashes 全部不变；
- 真实文本仅在本地 artifact；报告/终端只含 count/hash/category distribution；
- 所有条目均 `PENDING_USER_REVIEW`；Reranker v4 `approved=false/executed=false`，LLM 对该修订
  集合也未获批准。

## Reranker systematic v4 结果

- 审核窗口按用户授权唯一执行 v4：request 37、completed 36、failed 1、UNKNOWN 0、retry 0；
- 失败 revision ID `8b6b08e289b402b1f741`，positive rank 3/4，完整 4 项 order 已落盘；
- v4 checkpoint SHA-256：`e024ec8029370125116db5180f5ea0f12d699c388aa76ba3e4ded5cd294b901a`；
- 未重跑 v4；combined-v4 未生成，LLM-v3 request 0、results-v3 0；
- PASS 集合现为 39 条；未通过/未执行 positions 40–78 共 39 条。

## Systematic revision v5

- 对剩余 39 条一次性生成 two-term positive-only 推荐问题；
- 每条两个 term 均存在于 positive 且不在任一 distractor；39/39 非包含重叠，28/39 使用不同
  token family，其余按固定排序选择最优合规 pair；
- evidence documents/IDs/roles/locators/content hashes 逐项不变；
- review SHA-256 `6ecd5ef50fae97805aa35496dfbf795dfe6038e01ac876bf1cb10714954e68b2`；
- manifest SHA-256 `c6af47f4c19b704d57d80ad5c17dc95cd23f4c6a58080cb8eff14975266eeb80`；
- 39 条均 `PENDING_USER_REVIEW`；Reranker-v5 approved=false/executed=false，LLM 对 v5 集合
  未获批准。

## 边界

- 本次总模型 requests 2/批准上限 156；剩余预算因全局 Gate 失败不可继续使用；
- query Embedding 0、MinerU 0、Zilliz requests/writes 0；
- Docker 0、Git commits 0；
- 本次失败是评测业务 Gate，不是缺少配置；不得复用或重跑该 Reranker checkpoint。
- 未来 runner 已修复为先保存 order/rank/Gate 再转 FAILED；任何未来 v2 固定使用新
  `uat-reranker-v2.json`，当前 `approved_by_user=false`、checkpoint 不存在。
- 执行后完整质量门：286 tests passed、Ruff 283 files、mypy 105 source files、frontend、
  OpenAPI、配置检查、人工复核 artifact 复验与 secret scan 全部通过；failed 0、skipped 0。

STAGE_REVIEW_REQUESTED:REAL_UAT_MODEL_RESULTS
