# 自建 Milvus 与 Zilliz Cloud 切换

主开关位于 `config/.env`：`VECTOR_BACKEND=zilliz` 读取原 `ZILLIZ_CLOUD_*`，
`VECTOR_BACKEND=milvus` 读取 `VECTOR_*`。`local` 是 SQLite 离线开发适配器，
不代表自建 Milvus。真实服务均使用 `RAG_RUNTIME_PROFILE=production`。

## 本地配置

在主文件 `config/.env` 设置：

```dotenv
RAG_RUNTIME_PROFILE=production
VECTOR_BACKEND=milvus
```

填写 `config/.env.milvus`（模板为 `.env.milvus.example`，实际文件已加入 Git 忽略）：

```dotenv
VECTOR_URI=standalone:19530
VECTOR_DATABASE=default
VECTOR_COLLECTION=rag_chunks
VECTOR_USER=root
VECTOR_PASSWORD=<your_password>
VECTOR_TOKEN=
VECTOR_DIMENSION=1024
VECTOR_ENABLE_BM25=true
VECTOR_BM25_ANALYZER=chinese
```

将密码占位符替换为真实密码。地址支持 `host:port`、`http://host:port`、
`https://host:port`，客户端自动规范化无协议地址。禁止在地址中夹带凭据或查询参数，
也不接受本地 `.db` 文件路径。项目继续使用已有的 `pymilvus.MilvusClient`，
传入 `uri`、`db_name`、`user`、`password`，对应提供的连接方式。
用户名/密码与 Token 均为 SDK 支持的方式。[官方连接参数](https://milvus.io/docs/v2.6.x/connect-to-milvus-server.md)

如果使用 Token，清空 `VECTOR_USER`、`VECTOR_PASSWORD`，填写 `VECTOR_TOKEN`；
如果服务端未启用鉴权，三者均留空。配置不完整或同时指定两种认证方式会在连接前报错，
不会使用云端凭据兜底。配置报告、适配器状态均不输出密码。

优先级：**进程环境 > `.env.milvus` > `.env` > 类型默认值**。
覆盖文件只在主文件或进程环境选中 `milvus` 时读取，切回 `zilliz` 时忽略它。
覆盖文件仅允许 `VECTOR_*` 参数，不能放 `VECTOR_BACKEND` 或应用、模型配置。
该文件是可选的，也可将自建参数直接写在主 `.env` 或进程环境。
测试/导出显式指定 `load_env(env_path=...)` 时独立加载该文件，不混入本机覆盖文件。

地址必须从后端和 Worker 所在网络可访问：

| 运行位置 | 地址 |
|---|---|
| 与 Milvus 同一 Docker 网络 | 实际服务名，例如 `standalone:19530` |
| Windows 本机，Milvus 已映射 19530 端口 | `127.0.0.1:19530` |
| 其他服务器 | 实际 IP / DNS 名及端口 |

初始模板地址 `standalone` 在 Windows 上无法解析；用户随后已更新主配置，现已通过
网络、认证、数据库访问和中文 Analyzer 检查。2026-09-11 已在用户配置的
`test0911.rag_chunks` 初始化 Collection 和索引，并导入既有向量，见文末实测记录。

维度必须与 `EMBEDDING_DIMENSION` 及实际表结构一致，1024 不适合所有模型。
项目需要原生 BM25、中文 Analyzer、ARRAY 过滤及 `partial_update` 发布/权限更新。
自建实例需支持这些能力；其中部分更新要求 **Milvus 2.6.2+**。
[官方部分更新说明](https://milvus.io/docs/v2.6.x/upsert-entities.md)

## Docker Compose

填写两份本地配置后：

```powershell
docker compose -f compose.yaml -f compose.milvus.yaml up -d
```

该覆盖文件让后端和 Worker 都使用自建服务，并依次加载主配置和 Milvus 配置。
它连接现有实例，不创建 Milvus 容器或迁移数据。使用 `standalone` 服务名时，
后端和 Worker 必须加入它所在的 Docker 网络；Docker Desktop 通过宿主机已映射
端口访问时，可按实际部署填写 `host.docker.internal:19530`。

## 校验与初始化

历史脚本名保留 zilliz，实际目标由开关决定：

```powershell
.venv/Scripts/python.exe scripts/check_env.py --gate G2
.venv/Scripts/python.exe scripts/check_zilliz_readonly.py
.venv/Scripts/python.exe scripts/plan_zilliz_collection.py
```

只读检查不建表，创建计划不执行数据库操作。初始化命令为：

```powershell
.venv/Scripts/python.exe scripts/provision_zilliz_g2.py --approval ZILLIZ_COLLECTION_CREATE_APPROVED
```

它创建缺失的自定义数据库、Collection 和索引，加载表，用四份合成记录检查读写、检索、
权限过滤，再清理这些记录。已有表结构冲突时失败，不删除重建。
自建模式先连接 `default` 再创建/切换目标库，不套用旧云集群观测到的五个表容量限制。

**切换连接不等于迁移完成。** 旧云端向量不会自动出现在 Milvus。
既有知识库可在独立的新代际/目标 Collection 重建投影，核对 ACL 与水位并验收，
再切换服务及检索代际。若原文、维度、模型和 Schema 均兼容，也可在停止应用写入期间
精确复制原有投影，逐条对账正文、向量、版本、ACL 与水位，完整读回验证后启动新目标。
精确复制时可以保留原代际和主键；不能让应用在空库或部分导入阶段提供检索。
原件与解析结果仍在本地内容存储，这次适配不会重新解析或搬动它们。
更改配置后需重启后端与 Worker 才对已有进程生效。

切回云端时，设置 `VECTOR_BACKEND=zilliz`，恢复对应的已对账检索代际，
使用原 `ZILLIZ_CLOUD_*` 配置后重启相关进程。

## 适配范围

连接、建表 Schema、合成记录字段、加载、检索、写入、清理和配置校验现在统一读取所选后端参数。
原先仅支持 Token 的通用入口补齐用户名/密码；加载调用改为实际 SDK 的 `load_collection()`。
主密钥文件未提交；配置适配本身不会迁移数据或自动重启已有进程。
契约测试使用 SDK Schema 与隔离的测试客户端，不代替真实实例验收。

2026-09-11 验证结果：相关回归 127 项通过，4 项外部集成测试未选入；Ruff 通过，
mypy 检查 270 个源文件通过，Git 差异检查通过。Compose 覆盖文件完成 YAML 与两服务配置
一致性检查；本机没有 Docker CLI，未实际启动容器。用户后续已将主配置选为 milvus 并填好
连接参数；旧的未填写覆盖模板已改为注释，实际读取主配置。G4 配置检查通过。
服务返回版本为 3.0-20260902-658cbd1689、STANDALONE。

后续经用户授权，完成了初始化与导入：

- 目标 `test0911.rag_chunks`：30 个字段、12 个索引，已加载。
- 从现有云端复制 6,715 条向量记录，覆盖 13 个知识库、34 份文档；所有记录与 MySQL
  控制投影的原文、校验值和版本一致，所有显式标记已索引的记录均包含在内。
- 逐批写入后读回，并在结束时再次核对全量字段；保留已有向量、主键和代际，
  未重解析、未调用嵌入模型、未修改源云端；导入期间控制数据快照保持不变。
- 13 个知识库各抽取一份已发布文档进行 Dense/BM25 检索及范围检查，全部通过。
  该检查使用已有文档向量，不能代替自然语言端到端问答验收。
- 隔离合成记录的权限、有效期、代际过滤，以及发布、撤回、权限部分更新通过；
  更新后正文和向量保留，合成记录全部清理。
- 后端和 Worker 已重新启动并读取自建配置；恢复了原有 MySQL/Redis 服务，
  前端、后端健康接口和 Worker 心跳正常。
- 10 道真实问答均完成并通过程序检查，覆盖单/双/三文件、未知资料、隔离、十项清单、
  跨页表格、条件例外及跨文件综合。9 次有据回答、1 次按预期资料不足；复杂组辅助评审
  23/23 要点覆盖，共 80 次调用。管理员人工签核仍待复核，未声明全面质量验收或提速。

MySQL 的父块/展示投影和未索引历史记录不等同于向量记录数量，未强行写入这些额外行。
本次精确迁移与具体问答结果见本机 `artifacts/milvus-check-20260911/RESULT.md`；
该目录被 Git 忽略，不含连接凭据。迁移后如两端数据产生分叉，回切前需重新对账，
不能只切换开关并假定云端数据仍然最新。
