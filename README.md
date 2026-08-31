# 企业级 RAG 知识库

当前仓库只实施到 **G0 / 阶段 0 准备**。在审核任务返回 `APPROVED:G0` 前，
FastAPI、Celery/RabbitMQ、Milvus 及供应商模型都只以端口、Stub 和 Spike Harness
出现，不构成技术选型批准或真实能力验收。

## G0 快速验证

使用 Codex 工作区 Python 3.12 创建项目本地环境：

```powershell
& 'C:\Users\jcy\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' scripts/bootstrap.py
& '.\.venv\Scripts\python.exe' scripts/run_quality.py
```

配置优先级与安全边界：

1. 非敏感值：`config/user-input/project-inputs.yaml` 高于
   `config/defaults/stub-defaults.yaml`。
2. 密钥：进程环境变量高于 `config/user-input/.env.user.local`；校验器只返回变量名、
   来源和是否已配置，绝不返回值。
3. Stub 只解除本地开发依赖，不解除任何 Gate 阻断，也不产生真实性能或质量结论。

开发期进程全部直接由 Python 启动，不依赖容器。文件对象存储默认写入
`./data/storage`，并隔离为 `original/`、`artifacts/`、`quarantine/`、`temp/`；
真实 MySQL、Milvus、RabbitMQ 和 Redis 只能连接本机原生服务，否则使用明确的
本地 Stub。前端阶段批准后统一使用 `npm ci` / `npm run dev`。

详细证据见 `docs/stage-reports/G0.md`。
