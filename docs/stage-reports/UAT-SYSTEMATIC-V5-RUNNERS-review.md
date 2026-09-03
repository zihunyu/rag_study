# UAT systematic v5 Reranker/LLM runners 独立审核

审核结论：`APPROVED:UAT_SYSTEMATIC_V5_RERANKER_EXECUTION`

- 冻结执行计划 `uat-systematic-v5-execution-plan:v1` 的 SHA-256 为
  `54da69a95c3672ade39c1711641f5f67bdc3a3b69564d19877e0ff56f680b669`；
  `approved_by_user=true`、`executed=false`。
- 计划准确绑定 78 条 bundle：v1=1、v2=1、v3=1、v4=36、v5=39。全部 bundle
  SHA-256、source checkpoint SHA-256 与规范快照 SHA-256 均独立复核匹配；计划不含
  `question`、`content`、`answer`、API key 或 endpoint 字段。
- 独立定向测试 5 passed；覆盖 39 条 Reranker、严格组合 78/78、条件 78 条 LLM、恢复
  无重复、首条失败阻断、快照变异拒绝和 checkpoint 无正文。
- 未带 `--approved` 的 Reranker 运行被 `UAT_SYSTEMATIC_V5_EXECUTION_APPROVAL_REQUIRED`
  拒绝；未触发 provider 调用。
- 完整本地质量门 42 项全部通过，`failed=[]`、`skipped=[]`，包含 pytest、Ruff、mypy、
  前端、OpenAPI、配置、v5 计划冻结和 secret scan；secret scan finding=0。`git diff --check`
  无空白错误（仅行尾转换提示）。
- 执行前 `uat-reranker-v5` checkpoint、combined-v5 Gate、`uat-llm-v4` checkpoint 与
  `uat-results/v4` 均不存在或为空；本阶段 provider request=0、Zilliz write=0、Docker=0，
  无提交。

本审批只放行一次 v5 Reranker 阶段：最多 39 次、每条最多一次、top-2、automatic retry=0。
任一失败必须立即停止，combined Gate 与 LLM 请求均为 0，并提交结果复核。若 39 条均成功且
严格 combined-v5 Gate 达到 78/78，开发窗口必须先提交 Reranker 结果复核；审批窗口随后按已
有用户条件授权决定是否放行最多 78 次的 LLM v4。LLM 结果仍必须 `PENDING_USER_RESULT_REVIEW`，
不得自动标记 UAT 通过。
