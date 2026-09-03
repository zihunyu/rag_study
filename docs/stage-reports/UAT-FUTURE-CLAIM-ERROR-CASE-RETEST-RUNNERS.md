# Future structured-claim error-case retest runners 本地就绪审查

审查状态：`STAGE_REVIEW_REQUESTED:UAT_FUTURE_CLAIM_ERROR_CASE_RETEST_RUNNERS_READY`

## 动态范围与预检

- 选择器仅动态读取审核 JSONL 中 candidate review 的 `audit_verdict in {不通过, 待修订}`，本轮 selected=15，无 hardcoded case ID。
- 每个选中项仅从既有 bundle 读取原 question/locator，再从当前源节点重新解析 fresh evidence；旧答案和纠正引用不进入 case 输入。
- source-integrity preflight 输出 eligible=14、BLOCKED=1。BLOCKED 记录为独立、content-free 的本地 preflight 记录，provider calls=0，且不会阻止 14 条 eligible case。
- 14 个 controlled input case 文件均不含 answer 字段；input manifest 和 preflight blocked record 位于独立 `uat-future-error-retest-v1` artifact 根目录。

## Future-only 执行边界

- 使用 `FutureErrorCaseRetestRunner`，独立 checkpoint namespace `uat_future_error_retest_v1`，独立 result/audit/coverage revision `error-retest-v1`。
- future plan `uat-future-error-retest-plan:v1`：max provider requests=15、per-case max=1、automatic retries=0、`approved_by_user=false`、`executed=false`。
- 执行器在 plan 模式可验证 case/input hash；execution 模式同时要求 `--approved` 和 future plan 的明确批准。当前 future checkpoint/result/audit 根目录均不存在。
- 不运行 Reranker、Embedding 或 MinerU；不写 Zilliz；历史 v1–v5 results/checkpoints/Gate 严格只读。任何 future result 都保持 `PENDING_USER_RESULT_REVIEW`，不自动标记 UAT 通过。

## 本地验证

- 新增 dynamic selection/preflight 回归，并覆盖 independent retest namespace；通用/runner/retest 定向测试共 18 passed。
- 全量 pytest 收集 322 条；Ruff 348 files；mypy 112 source files；完整质量门 44 项 `failed=0`、`skipped=0`；frontend、OpenAPI、config 和 secret scan 均通过。
- 抗内容定向扫描覆盖 14 个新增/修改的 remediation/retest 文件；动态比较 78 个历史 ID、78 个答案和 17 个纠正引用，literal matches=0、20-hex case-ID literals=0。
- 历史 SHA 未变：Reranker v5 `188aded52174b60e399ea9d6448ffe383d5c553a050418581bfd20388e256317`；LLM v4 `7163f71791a974afac036e6eb8a6106f0de870275da47564b4d44359753cbb62`；combined-v5 Gate `ad5920fd2050797d4aaf3c952b68bffc515e9cc9adadb3a3dfd7f6eaf1f48d6b`。

本阶段 provider=0、Zilliz=0、Docker=0、模型重跑=0、commit=0。仅在独立审核和后续用户对 future plan 的批准后，才可执行最多 15 次的 future claim LLM。
