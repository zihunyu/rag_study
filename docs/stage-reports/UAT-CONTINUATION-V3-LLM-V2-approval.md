# UAT continuation v3 与条件 LLM v2 执行器审批

审批结论：`APPROVED:UAT_REMAINING_76_THEN_CONDITIONAL_LLM_78_RETRY_ZERO`

- Reranker v3 只处理 candidates 3–78，共 76 条；max requests 76、top-2、retry 0；
- Candidate 1 使用 v1 已通过证据，Candidate 2 使用 proposal 1/v2 已通过证据；
- 任一 v3 failure/UNKNOWN/Gate failure 立即停止，不生成组合 Gate，LLM 0；
- 组合 Gate 必须精确达到 78/78；
- 仅组合 Gate 通过后执行 LLM v2，max requests 78、retry 0；
- LLM 结果必须通过 citation Gate，仍需用户复核；
- v3/LLM-v2/combined Gate 文件当前均不存在；
- 独立定向审核 2 passed、Ruff、mypy 通过；开发完整质量门 294 passed；
- 本地准备网络调用 0、无提交、无 Docker。

用户真实执行授权已存在，允许按顺序执行。

