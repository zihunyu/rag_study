# 企业级 RAG 知识库

项目使用原生 Python 进程和 npm，不使用 Docker、Compose 或 Testcontainers。

## 配置

- 实际配置：`config/.env`，被 Git 忽略，禁止提交或复制到日志/聊天；
- 唯一模板：`config/.env.example`，包含全部类型化配置和逐项说明；
- 优先级：进程环境变量 > `config/.env` > 程序类型默认值；
- 检查命令只输出变量名、来源、是否配置和错误码，绝不输出值。

```powershell
& '.\.venv\Scripts\python.exe' scripts/check_env.py --gate G2
```

中间件只保留 MySQL 和 Redis。任务队列固定为 Python 本地 SQLite 持久队列。
向量数据库固定为 Zilliz Cloud 中国区，通过 `pymilvus.MilvusClient` 使用
`ZILLIZ_CLOUD_URI` 与 `ZILLIZ_CLOUD_TOKEN` 连接。

MinerU 使用 `MINERU_TOKENS` 英文逗号分隔 Token 池，支持 round-robin、单 Token
并发上限、连续失败、429 冷却和自动故障切换。任何状态、日志和异常均不得包含 Token。

LLM 可按部署需要使用 HTTP 或 HTTPS。`LLM_ALLOW_HTTP=true` 时允许两种协议；设为
`false` 时必须使用 HTTPS，或同时提供受信任内网/VPN 传输服务与审核证据。其他外部
AI 服务仍遵循 HTTPS 或受批准私有传输规则。当前 `ASR_ENABLED=false`，ASR/audio 由用户
暂缓，三项 ASR 配置留空且不阻断非 ASR G4；未来重新启用时恢复校验。

## G2 检索边界

- `/api/v1/search` 独立于问答生成；当前没有 `/ask`；
- 本地链路使用确定性 Embedding/Reranker、SQLite 控制面测试适配器与内存 Hybrid Index，
  输出 `real_acceptance=false`；
- MySQL/Redis、Zilliz Cloud 与 OpenAI-compatible 模型均有供应商适配和 Mock 契约，默认
  不连接、不写云资源、不发起计费调用；
- Zilliz 只读检查确认 database/Collection 尚不存在。精确创建计划可通过下列命令生成，
  但不会执行：

```powershell
& '.\.venv\Scripts\python.exe' scripts/plan_zilliz_collection.py
& '.\.venv\Scripts\python.exe' scripts/plan_model_probes.py
```

云端创建仍要求 `ZILLIZ_COLLECTION_CREATE_APPROVAL_REQUIRED`；模型单次真实探测仍要求
`BILLABLE_MODEL_CALL_APPROVAL_REQUIRED`。

## G3 可信问答与生命周期

- `/api/v1/ask` 使用 EvidencePackage、六业务状态、确定性 Mock 和服务端缓冲输出；不调用真实 LLM；
- `/api/v1/ask:stream` 在 verified 前只发送阶段进度，最终 result 才包含答案；
- 引用使用短时 HMAC 签名 URL，不在 URL 暴露内部文档、版本或 Chunk ID；
- 发布/回滚、ACL security transition、撤权、删除 tombstone、恢复不复活和追加式审计均为本地契约实现；
- MySQL G3 DDL 只有 plan，尚未获批在真实数据库执行；真实云端破坏性演练未执行；
- G3 评测集为固定 seed/revision 的六状态合成 Harness，始终 `real_acceptance=false`。

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

运行数据默认位于 `./data/storage` 并被 Git 忽略。G1/G2 的契约、解析器、适配器、
合成 Fixture 与 Harness 结果始终带 `real_acceptance=false`；当前 G4 门禁只计算文本 PDF、
扫描/图片、DOCX、PPTX、表格五类各 10 份。audio 为 `deferred_by_user`，历史契约保留但
不计入当前 ready，也不得宣称音频支持。

## 实现完成后的本地治理工具

- `python scripts/local_stack.py plan|start|status|stop`：原生 Python/npm 进程，无 Docker；
  stop 仅终止 PID/create-time/executable/command/cwd/owner-token 全部匹配的本项目子进程；
- `python scripts/plan_operations.py`：迁移、对账、备份、恢复和回滚 plan-only；
- `python scripts/generate_assurance.py --output ...`：离线 SBOM/许可证/依赖证据；
- `python scripts/generate_final_validation_plan.py`：最终统一真实验证计划，缺证据保持 BLOCKED。

管理端包含 diagnostics/alerts、Pilot Go/No-Go、synthetic canary/rollout/rollback、UAT、
7 天 observation、incident/defect/signoff 和 final acceptance dashboard。所有当前结果均为
`simulated=true`、`real_acceptance=false`，未进入真实 G5/G6 验收。
