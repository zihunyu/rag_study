# 真实 UAT Reranker / LLM 精确执行计划

状态：`EXECUTED_RERANKER_GATE_FAILED_LLM_NOT_STARTED`

计划阶段只构建 locator-grounded bundles、固定预算与两阶段安全 runner，当时所有外部请求
均为 0。独立审核批准后仅执行一次 Reranker；第 2 个请求的 positive 未进入 top-2，按全局
失败策略立即停止。Reranker 未重跑，LLM 未启动。

## 受控 UAT 定位

- approved candidates：78/78；用户“候选先审核”条件已满足；
- 这是 controlled locator-grounded UAT，不宣称完整生产检索 E2E；
- query Embedding requests 0、Zilliz requests 0；
- 每个 bundle 固定 4 documents：strict positive 1 + 同类、不同 sample/locator distractors 3；
- 类别分布：DOCX 20、扫描/图片 10、文本 PDF 10、PPTX 25、Spreadsheet 13；
- positive 从 expected locator 严格匹配的本地解析节点或 provider normalized nodes 聚合；
  任一候选匹配 0、内容空或不满足严格 coverage 会使整个 plan fail；
- distractors 使用稳定 hash 排序，同类且排除 positive sample，生成结果可重复；
- bundles 使用稳定 candidate/node/evidence IDs；v2 连续生成两次字节级一致；
- 真实 question/evidence 正文仅存 `LOCAL_STORAGE_ARTIFACTS_DIR/uat-bundles/v2`，计划与
  报告只含匿名 ID、locator hash、content hash、bundle hash 和计数。

匿名 plan：`artifacts/final-validation/real-uat-plan.json`。

## Reranker 阶段

- 固定 checkpoint：`uat-reranker-v1.json`；
- 最多 78 requests，每 candidate 1 次，automatic retries 0；
- 每条响应必须是 4 个 document index 的完整唯一排列；expected positive 必须进入固定 top-2；
- 任一请求、schema 或 positive Gate 失败：全局停止，禁止启动 LLM；
- 每条请求前持久化 UNKNOWN，确定 4xx/schema 记 FAILED，timeout/transport/5xx 保持
  UNKNOWN；恢复时已完成项不重复计费，FAILED/UNKNOWN 阻断继续；
- checkpoint 只含 candidate/evidence IDs、rank/order、Gate、status/code/type/trace hash，
  不含 query、documents、URL、key 或 provider message/body。
- bundle snapshot 绑定 candidate、question hash、按序 evidence/content/locator hash、positive ID、
  bundle hash/revision；manifest 同时绑定 revision、candidate count、预算、top-k 与 retry 参数。

## LLM 阶段

- 固定 checkpoint：`uat-llm-v1.json`；
- 只有 Reranker 78/78 completed 且 positive Gate 78/78 才能启动；
- 最多 78 requests，每 candidate 1 次，automatic retries 0；
- 输入仅为 approved question 与该 bundle 的授权 top-2 evidence；
- 输出必须包含 `status / answer / citation_ids`；citation 只能来自 bundle，且必须覆盖
  expected positive；额外来源、重复/未知 citation 或 schema 错误立即停止；
- 答案正文只原子保存在本地 `uat-results/v1`；checkpoint 仅保存 result/answer hash、
  citation IDs 与 Gate，不保存 answer/question/evidence 正文；
- LLM snapshot 额外绑定完整 Reranker 排序、positive rank 与 Reranker snapshot hash；输入、
  排序、revision 或 manifest 参数变化时均在新请求前拒绝恢复；
- 完成后状态仍为 `PENDING_USER_RESULT_REVIEW`，不得自动标记 UAT PASSED 或解锁最终 Gate。

## 精确预算与授权

- Reranker max 78；LLM conditional max 78；total model max 156；
- Reranker 任一失败时 LLM requests 固定 0；
- conditional user authorization satisfied：true；runner review 已完成；
- 实际 Reranker requests 2、completed 1、failed 1、UNKNOWN 0、Gate passed 1；retry 0；
- 失败码 `UAT_RERANKER_POSITIVE_NOT_IN_TOP_K`；第二次 Reranker execution false；
- LLM requests/completed/results 均为 0，checkpoint 不存在；
- 总批准上限 156，实际仅消耗 2；失败后剩余预算不得继续使用；
- NO_COMMITS、Docker 0、Zilliz write 0；
- 完整质量门：286 tests passed、Ruff 283 files、mypy 105 source files、frontend build、
  OpenAPI、配置检查与 secret scan 全部通过；quality summary `failed=[]`、`skipped=[]`。
- 计划连续生成两次 SHA256 一致；真实 UAT 两个 checkpoint 均不存在，执行调用仍为 0。

## 失败后本地补强

- v1 checkpoint 保持只读且 SHA-256 不变；旧 runner 未保存失败响应的完整排序，因此只标记
  `provider_order_unavailable=true`、`positive_rank_unknown=true`，没有推测或伪造；
- 失败候选的授权 question 与 4 份 evidence 已复制到受控本地人工复核 artifact，报告与终端
  只输出 ref/hash/count；
- 未来 runner v2 会在 Gate 判断前先持久化完整 ranked evidence IDs、positive rank、响应数量
  与 Gate；随后 gate=false 转为 FAILED 时保留这些安全字段且不保存正文；
- 任何未来重试固定使用 `uat-reranker-v2.json`，v1 checkpoint 只读；v2 当前
  `approved_by_user=false`、`executed=false`，不得执行。

STAGE_REVIEW_REQUESTED:REAL_UAT_MODEL_RESULTS
