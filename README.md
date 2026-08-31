# 企业级 RAG 知识库

项目使用原生 Python 进程和 npm，不使用 Docker、Compose 或 Testcontainers。

## 配置

- 实际配置：`config/.env`，被 Git 忽略，禁止提交或复制到日志/聊天；
- 唯一模板：`config/.env.example`，包含全部类型化配置和逐项说明；
- 优先级：进程环境变量 > `config/.env` > 程序类型默认值；
- 检查命令只输出变量名、来源、是否配置和错误码，绝不输出值。

```powershell
& '.\.venv\Scripts\python.exe' scripts/check_env.py --gate G1
```

中间件只保留 MySQL 和 Redis。任务队列固定为 Python 本地 SQLite 持久队列。
向量数据库固定为 Zilliz Cloud 中国区，通过 `pymilvus.MilvusClient` 使用
`ZILLIZ_CLOUD_URI` 与 `ZILLIZ_CLOUD_TOKEN` 连接。

MinerU 使用 `MINERU_TOKENS` 英文逗号分隔 Token 池，支持 round-robin、单 Token
并发上限、连续失败、429 冷却和自动故障切换。任何状态、日志和异常均不得包含 Token。

## 环境与启动

```powershell
& 'C:\Users\jcy\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' scripts/bootstrap.py
& '.\.venv\Scripts\python.exe' run_backend.py
& '.\.venv\Scripts\python.exe' run_worker.py
cd frontend
npm ci
npm run dev
```

质量检查：

```powershell
& '.\.venv\Scripts\python.exe' scripts/run_quality.py
```

运行数据默认位于 `./data/storage` 并被 Git 忽略。当前 Stub/Harness 结果始终带
`real_acceptance=false`，不能代替真实服务或格式 Gate。
