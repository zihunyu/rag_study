# UAT systematic v4 Reranker 与 LLM-v3 执行器审批

审批结论：`APPROVED:UAT_SYSTEMATIC_V4_RERANKER_75_THEN_LLM_V3_78_RETRY_ZERO`

- Reranker v4 只处理 75 条修订，max requests 75、top-2、retry 0；
- 组合 Gate 严格读取 v1/v2/v3 三条既有通过证据与 v4 75 条；
- 任一 v4 failure/UNKNOWN/Gate failure 时不生成组合 Gate，LLM 0；
- 组合 78/78 后才允许 LLM-v3 max requests 78、retry 0；
- LLM 引用必须来自 top-2 且覆盖 expected positive；结果仍需用户复核；
- v4/LLM-v3/combined Gate 文件当前不存在；
- 独立定向审核 5 passed；完整质量门 299 passed、Ruff、314 files format、
  mypy 108 source files；
- 无提交、无 Docker，本地准备网络调用 0。

用户真实执行授权已存在，允许按固定顺序执行。

