# 代码结构与使用入口

项目按全新部署维护一套当前实现。新增功能在对应模块中修改，模块、类、脚本、表名和契约文件
不使用 `v1`、`v2`、`v3`、`v4` 等迭代后缀；实现历史由 Git 管理。

## 目录职责

| 目录 | 用途 |
| --- | --- |
| `backend/src/ragkb/api` | HTTP 路由、请求模型与统一授权边界 |
| `backend/src/ragkb/application` | 上传、任务、发布、检索、问答和声明验收流程 |
| `backend/src/ragkb/domain` | 文档、权限、生命周期、数值事实与冲突规则 |
| `backend/src/ragkb/adapters` | MySQL、Redis、向量库、模型与身份供应商适配 |
| `backend/src/ragkb/infrastructure` | 当前数据库结构、本地持久化、检查点和验收文件 |
| `backend/src/ragkb/document_processing` | 解析、独立解析进程与分片 |
| `backend/src/ragkb/contracts` | 端口与当前 JSON Schema |
| `backend/src/ragkb/evaluation` | 可重复使用的离线质量、来源完整性和验收规则 |
| `backend/tests`、`frontend` | 后端回归，以及前端实现、单元测试与浏览器测试 |
| `scripts` | 初始化、检查、诊断和显式验收入口 |

## 常用入口

| 任务 | 命令 |
| --- | --- |
| 启动 API / Worker | `python run_backend.py` / `python run_worker.py` |
| 启动前端 | `cd frontend` 后运行 `npm run dev` |
| 后端离线回归 | `python -m pytest backend/tests` |
| 前端测试与构建 | `cd frontend` 后运行 `npm run check` |
| 综合质量检查 | `python scripts/run_quality.py` |
| 检查检索质量 | `python scripts/check_rag_quality.py` |
| 更新 / 检查 OpenAPI | `python scripts/export_openapi.py` / 加 `--check` |
| 初始化 MySQL | `python scripts/provision_mysql_g2.py --help` |
| 声明验收 | `python scripts/run_claim_acceptance.py --help` |
| 来源审核预检查 | `python scripts/prepare_claim_sources.py --help` |
| 真实端到端验收 | `python scripts/run_low_cost_real_acceptance.py --help` |

声明验收统一使用 `UatClaimRunner` 和 `ClaimArtifactStore`。用例是 JSON 数组，每条包含
`test_case_id`、`question`、`evidence`、`allow_cross_document`、`source_classification`。
证据包含来源文档、原文件哈希、正文和定位；独立渲染证明由现有来源完整性规则校验。

```text
python scripts/run_claim_acceptance.py --cases cases.json --run-id policy-check --max-requests 10
```

默认仅验证全部输入并输出不含正文的计划，不读取供应商配置、不写检查点、不调用模型。
实际执行需添加 `--execute --approved` 并满足配置和数据出境门禁。运行名称隔离检查点、结果、
审计和供应商幂等键；同名运行只能恢复同一输入快照，结果未知时不会自动重试。

## 数据与接口

业务接口统一为 `/api`，不保留 `/api/v1` 别名。健康与状态接口继续使用各自明确的路径。
MySQL 初始化包含 12 张当前业务表和 `schema_migrations`；实体使用行级 revision，任务仍有
续租、取消、fence token 和过期写入保护。SQLite 直接创建完整当前结构。

旧表迁移、逐代补列、旧租户 JSON 状态自动转换和固定历史批次执行脚本已移除。部署使用新的
数据库和存储目录。业务数据位于 `data`，供应商资源与本次目录清理无关。

真实文档版本、乐观锁 revision、索引 generation、内容哈希及签名密钥轮换属于业务或安全
身份，继续保留。依赖版本、OpenAPI 规范版本和供应商规定的 API / 模型名也继续保留。
`docs/adr` 保留架构决策，部署与维护说明位于 `docs/runbooks`、`docs/release` 等目录。

## 工作区清理

根目录保留 `README.md`、贡献指南、变更记录、许可证、源码入口、依赖锁和构建配置。
旧研究报告、开发计划、阶段审核记录，以及已有的构建、覆盖率、缓存和测试产物已清理。
这些清理项已移入系统回收站，可按需恢复。

`.git`、`.github`、`.devcontainer`、`.venv`、前端依赖和 `data` 继续保留。
运行测试或构建后，`artifacts`、`build`、缓存、覆盖率文件及前端 `dist` 等目录可能重新生成；
它们已被 `.gitignore` 排除，无需提交。
