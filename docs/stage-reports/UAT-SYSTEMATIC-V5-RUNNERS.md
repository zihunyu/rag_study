# UAT systematic v5 Reranker/LLM runners 本地审查

审查状态：`STAGE_REVIEW_REQUESTED:UAT_SYSTEMATIC_V5_RUNNERS_READY`

- 用户授权已冻结为：Reranker v5 仅 39 条、`max_requests=39`、top-2、自动重试 0；任一失败立即停止。LLM v4 仅在组合 Gate 达到 78/78 后执行，`max_requests=78`、自动重试 0；其结果必须保留 `PENDING_USER_RESULT_REVIEW`。
- 执行计划：`uat-systematic-v5-execution-plan:v1`，SHA-256 `54da69a95c3672ade39c1711641f5f67bdc3a3b69564d19877e0ff56f680b669`；`approved_by_user=true`，全局及两个 runner 的 `executed=false`。
- Reranker v5 独立 checkpoint 为 `provider-checkpoints/uat-reranker-v5.json`，namespace/idempotency 前缀均为 v5；LLM 使用独立 `provider-checkpoints/uat-llm-v4.json` 和 `uat-results/v4`。三项目前均不存在/为空，组合 Gate 也未生成。
- 组合 Gate 固定来源为 v1=1、v2=1、v3=1、v4=36、v5=39；它对每条 bundle 绑定 question hash、证据 content/locator hash、role、正例 ID、bundle SHA 与五个 source checkpoint hash。只有 78 条全部完成且 positive 位于 top-2 时才解锁 LLM v4。
- 审查输入哈希：v5 review `6ecd5ef50fae97805aa35496dfbf795dfe6038e01ac876bf1cb10714954e68b2`；manifest `c6af47f4c19b704d57d80ad5c17dc95cd23f4c6a58080cb8eff14975266eeb80`；v5 bundle snapshot `7e94b370d968077a46345587c9d6d33aa240bbd0d4b206a6564b99ea81834fd1`。既有 source checkpoint hashes 已逐项固定和复核。
- fake 端到端测试验证：39 条 rerank 成功后可严格组合 78/78；LLM 可处理 78 条；rerun 不重复请求；首条 rerank 失败时后续 rerank、组合 Gate 和 LLM 均不会继续；snapshot 变异在请求前拒绝；checkpoint 不含 question/content/answer。
- 恢复一致性：Reranker/LLM manifest 均绑定完整快照与预算；未知或失败 checkpoint 阻止重发；LLM 结果文件与 completed checkpoint 的 ref/SHA 双向一致性会在恢复前验证。
- 安全与质量门：全量 pytest 收集 304 条并通过；Ruff 325 files、mypy 109 source files、frontend、OpenAPI/config、v5 plan 模式及 secret scan 全部通过；完整质量摘要 `failed=0`、`skipped=0`。secret scan finding 0，计划不含正文、答案、API key 或 endpoint 字段。
- 本阶段 provider 请求 0，Zilliz 写入 0；无 Docker、无提交。ASR/IdP/7 天观察仍为 deferred。

审批后允许的唯一真实动作是运行 Reranker v5；LLM v4 仍需等待其 78/78 组合 Gate 成功。
