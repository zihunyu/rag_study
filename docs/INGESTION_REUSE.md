# 增量入库、向量缓存与复杂问题检索

本次功能作用于当前项目后端，不需要连接 0727。生产环境的上传、文档版本、权限和发布记录
仍以 MySQL 为准；队列使用 Redis。新增的本机 SQLite 文件只保存可验证的复用记录和向量缓存，
适用于项目当前的单实例部署，API 和 Worker 使用相同的 `LOCAL_STORAGE_ROOT`。

## 文档与问题向量缓存

默认启用 `EMBEDDING_CACHE_ENABLED=true`、`QUERY_EMBEDDING_CACHE_ENABLED=true`。
两者分别控制文档和查询路径，数据保存于 `LOCAL_STORAGE_ROOT/cache/embeddings.sqlite3`。

- 输入按完整 UTF-8 文本计算哈希，保留空格、标题等输入差异；相同批次内也会去重。
- 缓存身份包含提供方地址、模型、维度、嵌入配置和 `EMBEDDING_CACHE_REVISION`。
  提供方原地更换模型权重时，应增加这个 revision，避免复用旧结果。
- 不保存输入正文；缓存向量具有完整性校验，损坏或不符合维度的记录不会返回给调用方。
- 每个成功批次立即持久化。后续批次或写向量库失败，重试可复用已经算好的批次。
- API、Worker 通过有界文件锁协调相同输入；锁等待受超时限制，失败不会静默触发重复计算。
- 缓存不会复用检索结果、权限判断或答案。每次问答仍执行当前文档权限、版本和引用检查。

首次处理新文本仍会产生 Embedding 用量。历史 Milvus 向量不会自动导入缓存；缓存会随新入库和
问答逐步积累。删除缓存会降低后续命中率，但不会删除知识库。

`/api/system/status` 的 `embedding_cache.process_counters` 提供当前 API 进程的命中、缺失和
请求批次数；Worker 的计数不包含在此进程统计中。实际模型费用继续查看已有用量记录。

## Embedding 批处理

新增 `EMBEDDING_MAX_INPUT_TOKENS=8192`、`EMBEDDING_MAX_BATCH_TOKENS=32768`，
与原有 `EMBEDDING_BATCH_SIZE` 同时生效。这些是本地预算，应按实际提供方限制调整。
生产装配使用固定版本 tokenizer 计数，不将其当成供应商账单计数。

调用前检查整组输入，超限单条直接报 `EMBEDDING_INPUT_TOKEN_LIMIT`，不截断正文。
每批响应必须提供完整、不重复的整数 `index`，按该字段恢复原始输入顺序；序号、数量、
维度或有限数值校验失败时拒绝该批结果，不写缓存。

## 目录增量同步

入口为仓库根目录下的 `scripts/sync_directory.py`，默认只预览。
这是本机运维入口，使用当前配置的知识库服务身份。目录和目标知识库必须明确指定。

```powershell
# 先预览，报告列出新内容、更新、复用、别名和移除的源路径。
.\.venv\Scripts\python.exe scripts/sync_directory.py "E:\资料目录" --space-id "目标知识库ID" --report "artifacts/sync-preview.json"

# 执行同一目录的增量上传，提交现有 Worker 队列。
.\.venv\Scripts\python.exe scripts/sync_directory.py "E:\资料目录" --space-id "目标知识库ID" --apply --report "artifacts/sync-result.json"
```

只上传变更内容，仍经过文件类型、大小、哈希、隔离与恶意文件校验。成功入队不代表已经解析、
索引或发布完成；任务中心可以继续查看处理结果，复核与发布沿用原有流程。

- 按文件内容、格式和处理合同去重，记录同一内容的所有相对路径。
- 未变化且文档/任务记录仍有效时直接复用，包括已经在队列中的任务，不重复提交。
- 单一来源更新时创建同一文档的新版本。共享内容的某个副本变化时创建独立文档，保留其他副本。
- 文件改名可以复用原内容；内容回退到旧版本时会创建新的待审版本，不能误用过期版本。
- 文件删除只记录 `removed_sources`，不自动删除或下架已发布文档。
- 失败、取消的任务会报告为 `failed`；明确添加 `--retry-failed --apply` 才创建新的处理版本。
- 支持中断后恢复上传意图；已成功提交的内容会逐项登记，避免下次从头重传。
- 不跟随符号链接和目录联接；扫描遇到读取错误会失败，不将未扫描到的目录当作空目录。

同步登记文件位于 `LOCAL_STORAGE_ROOT/sync/directory.sqlite3`。预览也会核对 MySQL/队列中的
真实状态，不能仅凭本地记录将任务视为成功。第一次同步尚未登记的目录，需要按新的处理合同入库；
不会直接假定以前手动上传的文件可复用。每次操作前先查看预览报告。

默认扫描上限由 `DIRECTORY_SYNC_MAX_FILES=10000` 控制。解析器、分块器及相关配置改变时，
旧内容可能需要重新处理；外部处理行为变化但版本标识未变化时，增加 `DIRECTORY_SYNC_CONTRACT_REVISION`。
调整单价、并发数、重试次数或查询缓存开关，不会使已登记资料重新解析。

## 复杂问题的多个检索任务

默认 `RETRIEVAL_QUERY_PLANNING_ENABLED=true`、`RETRIEVAL_MAX_SUBQUERIES=4`。
上限包含原始问题。规则识别多个问题、枚举关注点以及明确的比较对象；单一问题不展开。

拆分在本地进行，不调用 LLM。每个检索子任务保留完整原问题，并追加关注点，避免丢失实体、
否定、时间和条件。任务固定使用同一个检索版本与权限上下文，候选按排名融合、去重，经过授权后
统一重排，再进入现有回答与独立核验流程。失效的入库尝试也继续按原有规则过滤。

复杂问题首次执行可能产生更多查询向量请求，这换取了更多方面的证据；重复问题可命中查询向量缓存。
如需单查询成本，将上限设为 `1` 或关闭规划开关。并非所有自然语言问题都能被规则拆分，
本次实现不保证每个问题的正确率都会提高。

## 验证

```powershell
.\.venv\Scripts\python.exe -m pytest -q backend/tests/test_embedding_reuse.py backend/tests/test_directory_sync.py backend/tests/test_query_planning.py backend/tests/test_dependency_boundaries.py
.\.venv\Scripts\python.exe -m pytest -q
```

用例覆盖响应乱序与非法序号、双重批次预算、进程重建后的缓存复用、并发去重、缓存损坏、
查询缓存开关、目录去重/改名/更新/中断恢复/失败重试/内容回退，以及多个检索任务的权限过滤与统一重排。
