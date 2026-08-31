# G0 第一次审核结论

审核结果：`CHANGES_REQUIRED:G0`

审核日期：2026-08-31

## 已通过

- 未发现 Dockerfile、Compose 文件或容器启动依赖；现有 Docker/Testcontainers 文本均为禁止规则或历史材料。
- `py -3.10 scripts/run_quality.py` 独立重跑通过：23 tests、Ruff、npm check、原生入口和五类 Harness 均通过。
- 本地文件存储具有分区、路径边界和原子 replace 测试。
- Stub/Real Integration 分层清楚，Stub 结果带 `real_acceptance=false`。
- `.env.user.local` 未被输出，报告只包含密钥变量名和配置状态。

## 必须修正

1. Codex 工作区存在 Python 3.12.13：
   `C:\Users\jcy\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe`。
   该解释器直接运行质量门因缺少项目依赖而失败。必须提供本地 `.venv`/bootstrap 流程，使用 3.12 安装项目及 dev 依赖，并在 3.12 下通过 pytest、Ruff、mypy、原生入口和 Spike；正式 Gate 不得把 mypy 记为可忽略的 SKIP。
2. `_load_secret_statuses` 对进程环境仅判断非空，`LLM_API_KEY=__FILL_ME__` 等仍会被误报为已配置。进程环境和本地 env 文件必须使用相同的占位值判定，并增加回归测试。
3. G0 报告声明正式 Gate 未就绪，却仍提交 `STAGE_REVIEW_REQUESTED:G0`。完成以下 ADR/计划重基线后才能重新申请：
   - 生产目标仍优先 Milvus 原生 BM25，真实中文/ACL/watermark 验收移到 G2；G1 只允许本地词法适配器，不能冒充 Milvus。
   - 按无 Docker、本地 Python 运行约束，把 R1 默认任务队列改为 Python 本地持久队列（建议 SQLite/文件日志）并保留可替换端口；RabbitMQ/Celery 只能作为可选原生适配器，不得作为启动前置。
   - 用户配置已把 XLS/XLSX/CSV 和音频纳入 R1。必须同步修改完整开发计划、WBS、Gate、工期、黄金样本和风险；不得继续沿用“R1.1 在 G6 后”的旧基线。
4. 60 份真实样本当前为 0。为避免阻止配置无关开发，可以把 G0 退出条件重基线为“样本清单、采集责任和 Harness 就绪”，但真实格式效果必须成为 G1/G2 的硬门禁；计划、报告和追踪矩阵必须一致，且不得声称格式已支持。
5. Docker 禁止扫描当前只扫描部分实现目录。文件名检查应覆盖整个仓库；命令内容检查至少覆盖根脚本、backend、frontend、scripts、deploy 和 CI 配置，并用明确排除项避开历史计划/禁止规则自身。
6. 更新 `docs/stage-reports/G0.md`：删除“目标 Python 3.12 未安装”的错误结论，记录 3.12 环境和依赖安装结果；在所有 ADR、计划和质量证据一致且 `gate_ready=true` 后再提交。

## 用户输入边界

本轮不要求填写 LLM、Embedding、Reranker、ASR 或 MinerU Token。真实集成密钥继续按 G1/G2/G3 条件阻断，不能阻塞本轮技术修正。

