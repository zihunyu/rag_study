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
& '.\.venv\Scripts\python.exe' scripts/check_env.py --gate G2
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
- `LLM_ALLOW_HTTP=true` 时 LLM 地址允许 HTTP 或 HTTPS，由部署方承担明文传输风险；
- `LLM_ALLOW_HTTP=false` 时 LLM 与其他 external AI 地址一样，必须使用 HTTPS，或在
  `AI_TRUSTED_PRIVATE_TRANSPORT_SERVICES` 和
  `AI_TRUSTED_PRIVATE_TRANSPORT_EVIDENCE` 同时提供已批准内网/VPN 传输证据；
- Zilliz Cloud URI 必须为中国区 HTTPS Endpoint；
- Token、密码和连接密钥不得出现在日志、异常、状态报告或 Git 中。

## Gate

缺少真实密钥不阻止本地代码和 Stub 测试，但按条件阻止对应真实 Gate。SQLite 队列属于
本地任务基础设施，不替代 MySQL 控制面、Redis 缓存或 Zilliz Cloud 检索的真实验收。
用户当前将 ASR/audio 暂缓：`ASR_ENABLED=false`，ASR 三键允许为空且不阻断 G4 非 ASR
范围。原始完整范围保留六类 60 槽位历史基线；当前只计算五类非 ASR 格式各 10 份，
共 50 份。音频离线契约/Stub 与未来恢复入口保留，但不继续开发或声明支持。

## G5/G6 实现边界

Pilot、灰度、UAT、7 天观察期、incident/defect/signoff 和最终验收报告均实现为 SQLite +
API + Vue 的本地合成流程。真实五类样本、真实模型、MySQL migration 和外部 lifecycle drill
统一留到 `FINAL_UNIFIED_VALIDATION`；它们不再阻断代码开发，但缺失时最终报告必须 BLOCKED。

G2 的 Zilliz 与模型默认保持 Dry-run：只读检查可执行；database/Collection/Schema/索引
创建、数据写入和计费模型探测必须分别取得明确批准。本地 `/search` 使用 Mock/测试适配器，
不代表真实 Zilliz 或供应商验收。
