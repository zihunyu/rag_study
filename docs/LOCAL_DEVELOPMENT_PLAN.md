# 本地开发配置与启动

## 唯一配置入口

| 文件 | 用途 | Git |
| --- | --- | --- |
| `config/.env` | 当前机器实际配置，含密钥 | 必须忽略 |
| `config/.env.example` | 唯一模板和全部字段帮助文案 | 跟踪 |

进程环境变量覆盖 `config/.env`。`python scripts/check_env.py` 只显示变量名、类型、
来源、配置状态和错误码，不显示值。

## 原生启动

```powershell
& '.\.venv\Scripts\python.exe' scripts/check_env.py --gate G1
& '.\.venv\Scripts\python.exe' run_backend.py
& '.\.venv\Scripts\python.exe' run_worker.py
& '.\.venv\Scripts\python.exe' run_mineru.py --check
cd frontend
npm ci
npm run dev
```

## 固定架构边界

- 中间件：MySQL、Redis；
- 队列：Python 本地 SQLite 持久队列，不提供外部消息队列配置或适配器；
- 向量库：Zilliz Cloud 中国区，配置键仅使用 `ZILLIZ_CLOUD_*`；
- 文件：`LOCAL_STORAGE_*` 下的本地分区；
- MinerU：`MINERU_TOKENS` 多 Token round-robin 池；
- 无 Docker、Compose、Testcontainers 或容器启动路径。

## 安全条件

- `restricted` 禁止出站；
- internal/confidential 的外部 AI 地址必须是 HTTPS；
- 明文 HTTP 只可在 `AI_TRUSTED_PRIVATE_TRANSPORT_SERVICES` 和
  `AI_TRUSTED_PRIVATE_TRANSPORT_EVIDENCE` 同时提供已批准内网/VPN 加密证据时使用；
- Zilliz Cloud URI 必须为中国区 HTTPS Endpoint；
- Token、密码和连接密钥不得出现在日志、异常、状态报告或 Git 中。

## Gate

缺少真实密钥不阻止本地代码和 Stub 测试，但按条件阻止对应真实 Gate。SQLite 队列属于
本地任务基础设施，不替代 MySQL 控制面、Redis 缓存或 Zilliz Cloud 检索的真实验收。
