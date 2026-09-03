# Future structured-claim error-case retest runners 本地就绪审查 r2

审查状态：`STAGE_REVIEW_REQUESTED:UAT_FUTURE_CLAIM_ERROR_CASE_RETEST_RUNNERS_READY`（修订 `r2`）

## r2 通用安全闭环

- future retest case 现在强制携带 source classification，并在真实 `execute --approved` 路径创建 HTTP transport 前调用既有 provider egress policy，校验 outbound flag、classification allowlist 和 approved processing region。region 缺失与受限 classification 的 fake 拒绝均有回归覆盖。
- 需要视觉/文本层一致性的 fresh evidence 强制 rendered proof。preflight 要求完整的 locator-aligned verified render representation；缺失、控制字符或 source/render 不一致均动态形成 content-free `BLOCKED`，provider=0，不进入 LLM，也不阻断其他 eligible case。
- render-proof 契约升级为独立 `uat-future-error-retest-v2` input/plan/runner namespace，避免覆盖 v1 preparation。v2 runner/checkpoint/result/audit/coverage revision 为 `error-retest-v2`。

## 动态输入与预算

- 审核 JSONL 动态选择 15 条需重测 case；v2 source-integrity/render preflight 为 eligible=14、BLOCKED=1。
- 14 个 eligible controlled case 均同时具有 classification 与 rendered proof；输入不含旧 answer。BLOCKED 记录 provider call count=0。
- v2 plan：max provider requests=15、per-case max=1、automatic retries=0、`approved_by_user=false`、`executed=false`。future v2 checkpoint/result/audit 根目录均不存在。

## 验证与隔离

- 定向 tests=20 passed，覆盖 classification egress policy、missing render proof BLOCKED、dynamic selection、future contract/coverage resume、跨文档策略与不可变 audit。
- 全量 pytest=324 collected；Ruff=350 files；mypy=112 source files；完整质量门=44 checks，`failed=0`、`skipped=0`；frontend、OpenAPI、config 与 secret scan 全部通过。
- 抗内容扫描覆盖 14 个 remediation/retest 文件；动态比较 78 个历史 ID、78 个答案和 17 个纠正引用，literal matches=0、20-hex case-ID literals=0。
- 历史 SHA 未变：Reranker v5 `188aded52174b60e399ea9d6448ffe383d5c553a050418581bfd20388e256317`；LLM v4 `7163f71791a974afac036e6eb8a6106f0de870275da47564b4d44359753cbb62`；combined-v5 Gate `ad5920fd2050797d4aaf3c952b68bffc515e9cc9adadb3a3dfd7f6eaf1f48d6b`。

本阶段 provider=0、Zilliz=0、Docker=0、模型重跑=0、commit=0。v2 future execution 仍需新的独立审核和用户批准；任何结果仍必须 `PENDING_USER_RESULT_REVIEW`，不得自动标记 UAT 通过。
