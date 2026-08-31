# G0 第二次审核结论

审核结果：`CHANGES_REQUIRED:G0`

## 已通过

- `.venv` Python 3.12.13 全质量门独立重跑通过：30 tests、Ruff lint/format、mypy strict、npm check、全部原生入口；0 failed、0 skipped。
- 配置 `gate_ready=true`，G0 decision/schema/user blocker 均为 0。
- 24 周/12 Sprint/270 人周、表格与音频并入 R1、Milvus G2 真实门和本地持久队列已同步到计划、ADR、WBS、Gate、风险和样本清单。
- 全仓无 Docker 扫描及四项专用测试通过。
- 进程环境与本地 env 的占位密钥判定已统一并有回归测试。

## 唯一待修正项

当时的字段 Gate 映射与重基线不一致；该旧映射文件已在后续配置迁移中删除：

- `ai_services.asr.{provider,endpoint,model_id}` 和 `ASR_API_KEY` 当前标为 G1，计划与阶段报告均规定音频真实门在 G2；必须改为 G2。
- `ai_services.llm.model_revision`、`timeout_seconds`、`max_concurrency` 被通用规则标为 G2，但 LLM 真实集成在 G3；必须增加 LLM 专用 G3 规则，避免被通用 `ai_services.*` 规则提前阻断。
- 增加 G1/G2/G3 配置报告回归测试，证明：G1 不因 ASR/LLM 阻断，G2 只因 ASR、Embedding、Reranker 和真实 Milvus 等条件阻断，G3 才纳入 LLM 条件。

修正后重跑 Python 3.12 全质量门并重新提交，无需修改其他已通过内容。
