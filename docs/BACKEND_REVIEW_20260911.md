# 后端继续审查：增量入库、检索覆盖和 0727 可借鉴实现

后续实施补记（2026-09-11）：第三项“子问题独有证据在重排前丢失”已加入授权后的子问题候选名额保护；多个问题批量向量化/检索、必答项到证据及答案的覆盖回执、包含 Worker 的持久化任务统计也已实现。说明与接口见 [复杂问答与任务统计](COMPOUND_QA_AND_REUSE.md)。下文表格和复现数字保留为原始审查记录，其他未列入本次实施的问题仍待处理。

后续修复（2026-09-11）：下文第二项“问题拆分与补充检索叠加”已修复。上限现在由整轮问答共享，
原失败样本从实际检索 12 次、记录 3 条，变为实际检索 4 次、记录 4 条。
新增 14 项预算测试通过，全量回归 1751 passed、7 skipped、9 deselected。
此处为当时的修复记录；以下复现数据是修复前记录，最新实施状态见顶部补记。
修复用例：[问答检索预算](E:/Data/codex/20260831rag/backend/tests/test_retrieval_query_budget.py)。

审查日期：2026-09-11。当前项目：`E:\Data\codex\20260831rag`；对照源码：`E:\Data\codex\0727\rag-api\backend`。
本轮只修改审查文档、保存离线复现用例；没有修改应用源码、生产配置、知识库或服务进程。
0727 的结论基于上述源码，不代表其某个历史 release 当前已加载全部实现，也不代表已测得两套系统的真实问答准确率。

结论：下一批应先修复已经落地的复用与检索边界，再增加新能力。现有 28 项专项回归全部通过，但本轮新增的 5 个边界用例均未通过，说明原测试覆盖存在缺口。复现全部使用临时 SQLite、临时文件、确定性向量或假 HTTP transport，没有调用付费模型，也没有导入真实资料。

| 优先级 | 已复现问题 | 实际结果 | 修正方向 |
|---|---|---|---|
| P1 | 成功任务被清理后，未变化资料重复入库 | 模拟成功任务记录消失后，同步预览为 `updated`；执行后同一文档从 1 个版本变成 2 个 | 对已完成内容使用持久化处理凭据；队列状态只管理尚未完成的任务 |
| P1 | 多问题规划与补充检索叠加 | 配置 `max_subqueries=4`，一次证据组装实际发起 12 组底层检索，持久化查询列表只记录 3 条 | 问答级共享预算、查询去重、完整查询轨迹；补充查询消耗同一预算 |
| P1 | 子问题独有证据在重排前丢失 | 凭证子问题检索第 1 名是发票材料，但 40 条重复出现在多个检索列表的保修材料把它挤出重排候选池 | 保留子问题来源标识，先为必要子问题分配候选，再融合和重排 |
| P2 | 上传会话过期后，同步恢复被旧意图卡住 | 中断上传后使会话过期，恢复报 `UPLOAD_SESSION_EXPIRED`；重复执行继续用相同旧会话 | 对过期且未完成的会话更新上传意图，重新取得文档行版本；保留已完成会话的幂等保护 |
| P2 | 语义分块先嵌入整个原始段落，超长段落无法分块 | 9000 token 段落可以由结构分块处理；语义分块在生成小块之前报 `EMBEDDING_INPUT_TOKEN_LIMIT` | 语义边界判断先使用有预算的文本窗口；保留完整正文和位置，不截断最终资料 |

第一项直接影响长期使用：生产 Redis 队列的成功、取消任务保留 7 天，失败任务保留 30 天，worker 的队列扫描会执行清理。当前 `_live()` 在任务不存在时返回不可复用，因此本地持久向量缓存虽然仍可能节省 Embedding，重复解析、重新建版本与写索引依然会发生。复现没有改动生产 Redis，而是模拟其真实清理后的返回值。

证据：[队列保留和清理](E:/Data/codex/20260831rag/backend/src/ragkb/adapters/redis_queue.py:113)、[复用判断](E:/Data/codex/20260831rag/backend/src/ragkb/application/directory_sync.py:118)。0727 则核对持久内容修订和活动 generation 的 manifest，不依赖短期任务是否还存在：[同步复用](E:/Data/codex/0727/rag-api/backend/kb_v2/sync.py:95)。

第二项不是每次问答都会发生：初次检索能覆盖问题时没有补充轮次；补充语句是单问题时也不会再次展开。但当前 4 次上限只作用于一次 `search()`，并不是整轮问答上限。现有证据选择器最多再调用两次 `search()`，每次都可能再次拆分为 4 个任务；重复问题的向量缓存只能缓解模型请求，不能消除全部检索、融合和选择开销。

证据：[每次 search 规划](E:/Data/codex/20260831rag/backend/src/ragkb/application/search.py:326)、[补充轮次](E:/Data/codex/20260831rag/backend/src/ragkb/application/evidence.py:355)、[只记录外层 queries](E:/Data/codex/20260831rag/backend/src/ragkb/application/evidence.py:343)。

第三项复现使用当前默认候选规模：每路 50、重排 40、最终证据 8。问题不是权限过滤，也不是重排模型判断错误，而是独有证据到不了重排步骤。当前跨查询融合的候选没有保留 facet 身份；同一方面在多个查询中反复出现时，累计得分会优先占满候选池。

证据：[跨查询融合](E:/Data/codex/20260831rag/backend/src/ragkb/application/query_planning.py:58)、[重排池截断](E:/Data/codex/20260831rag/backend/src/ragkb/application/search.py:416)。0727 有按 facet 轮流补足证据的实现，可借鉴通用规则：[分方面选择](E:/Data/codex/0727/rag-api/backend/kb_v2/retrieval.py:370)。

第四项源于同步意图一直复用原来的 operation，而上传层正确地拒绝已过期会话。应在同步恢复层续建意图，不应取消上传层的过期检查。[旧意图复用](E:/Data/codex/20260831rag/backend/src/ragkb/application/directory_sync.py:242)、[会话过期检查](E:/Data/codex/20260831rag/backend/src/ragkb/application/uploads.py:72)。

第五项目前不影响正在使用的分块模式：只读检查实际配置为 `CHUNK_STRATEGY=structure`。它会在切换为 `semantic` 后暴露。语义评分器还把所有见过的文本和向量长期保存在进程字典，未看到逐文档释放或容量限制；大批量入库前应一起处理。[整段预加载](E:/Data/codex/20260831rag/backend/src/ragkb/document_processing/chunking.py:435)、[评分器内存缓存](E:/Data/codex/20260831rag/backend/src/ragkb/document_processing/chunking.py:553)。

接下来值得借鉴的功能如下。表中是实施建议，尚未在本轮修改应用实现。

| 优先级 | 借鉴项 | 0727 的实际做法 | 对当前项目的增量价值 | 是否需要全库重新向量化 |
|---|---|---|---|---|
| 最高 | 必答项、证据和最终答案的对应检查 | `AtomicQuestion` 定义必要问题；证据有 aspect ID；最终通过核验的 claim 要覆盖 required output ID | 当前已有充分性选择、主张核验和条件核验，但尚未把新拆分任务贯穿到最终输出合同。补上后可明确告诉用户哪部分有答案、哪部分资料不足 | 不需要 |
| 高 | 多个问题向量合并请求、批量向量检索 | `search_many()` 一次读取多个问题缓存，将缺失问题批量 Embedding，再一次 Milvus search 传入多条向量 | 当前每个 facet 顺序执行一次查询向量获取和检索；可以减少往返、改善首问延迟。批处理本身不减少唯一文本 token 数 | 不需要 |
| 高 | generation 与 Embedding 模型合同绑定 | 检索前比较 generation 的 embedding version；Milvus filter 同时限制 embedding version | 当前新缓存已按模型区分，但向量索引记录/检索过滤没有同等模型合同。更换成同维度模型时，缓存隔离不能保证旧索引与新问题向量兼容 | 增加检查不需要；真正换模型时才评估迁移 |
| 高 | 入库复用量和成本过程可见 | 记录 `embedded_new`、`cached_reused`、进度，以及问题缓存命中/新增和供应商 token 来源 | 当前状态接口只有 API 进程计数，重启归零，Worker 不在统计内。应增加按任务、知识库持久统计，显示实际复用和新增请求 | 不需要 |
| 中 | 表格汇总和计算的确定性执行 | 支持带类型的计算；通用表格路径有去重、数量/合计的派生审计；派生结果本身不可当成原文引用 | 当前已有数值、单位和范围核验，可在此基础上增加从源行到计算式再到答案的证据链，改善“共多少、差多少、合计多少”问题 | 通常不需要；缺少源行结构的资料单独评估 |

借鉴源码：[必要问题定义](E:/Data/codex/0727/rag-api/backend/rag/generic_query_planner.py:65)、[必要证据覆盖检查](E:/Data/codex/0727/rag-api/backend/rag/generic_answering.py:881)、[最终输出覆盖检查](E:/Data/codex/0727/rag-api/backend/rag/generic_answering.py:3428)、[批量问题向量](E:/Data/codex/0727/rag-api/backend/kb_v2/vector.py:302)、[模型版本检查](E:/Data/codex/0727/rag-api/backend/kb_v2/retrieval.py:132)、[入库复用进度](E:/Data/codex/0727/rag-api/backend/kb_v2/vector.py:280)、[通用表格派生审计](E:/Data/codex/0727/rag-api/backend/rag/generic_answering.py:2810)、[带类型计算](E:/Data/codex/0727/rag-api/backend/rag/deterministic_compute.py:19)。

模型合同缺口属于静态审查结论，未通过实际更换生产模型进行实验。当前向量记录包含 generation、analyzer revision 和正文校验值，但没有 embedding 模型身份；检索过滤同样没有模型条件：[写入记录](E:/Data/codex/20260831rag/backend/src/ragkb/adapters/vector_indexing.py:281)、[检索过滤](E:/Data/codex/20260831rag/backend/src/ragkb/adapters/zilliz.py:90)。维度相同只表示数组长度相同，不能替代模型兼容性校验。

另有两项适合大资料量场景的优化，需要压测后决定规模：

- 向量缓存提供批量读取、容量/空间统计和明确的清理策略。当前每个输入单独建立 SQLite 连接查询，向量以 JSON 保存，无条目上限或过期机制。不要机械设置很短 TTL，否则会抵消持久复用的省费目的。[缓存读写](E:/Data/codex/20260831rag/backend/src/ragkb/adapters/embedding_cache.py:61)。
- 优化精确答案缓存的知识库快照计算。当前每次计算 key 会读取整代分块投影，并校验已验证图片的实际字节；命中前还会再次核对。安全检查完整，但大库下重复提问仍可能有明显数据库和磁盘开销。可研究事务维护的内容/权限/发布时间版本摘要，前提是覆盖所有修改和时间生效边界，不能简单删除检查。[完整快照](E:/Data/codex/20260831rag/backend/src/ragkb/infrastructure/qa_snapshot.py:44)。这项尚未压测，不能给出延迟提升比例。

实施顺序建议：先修任务过期复用和上传恢复，再统一问答检索预算与 facet 覆盖，接着做批量查询向量与持久统计，最后按真实题型补表格计算。上述第一批修复不需要重导原始资料或全库重新向量化。

当前项目已有的权限隔离、发布和版本约束、父子块定位、证据充分性检查、独立核验、数值条件核验、会话持久化、评测体系应继续保留。0727 的游戏专用实体和伤害规则不宜直接搬入通用知识库；这里只借鉴通用合同、选择策略和带来源的计算方式。

验证材料：

- [5 个边界复现用例](E:/Data/codex/20260831rag/artifacts/backend-review-20260911/test_review_repro.py)：本轮在正常测试目录外保存，断言期望行为，当前均失败；没有伪装成已修复测试。
- [复现输出](E:/Data/codex/20260831rag/artifacts/backend-review-20260911/repro.log)、[复现 JUnit](E:/Data/codex/20260831rag/artifacts/backend-review-20260911/repro.xml)。
- [已有专项回归](E:/Data/codex/20260831rag/artifacts/backend-review-20260911/existing-regressions.xml)：28 passed。
- [源码指纹及检查范围](E:/Data/codex/20260831rag/artifacts/backend-review-20260911/source-snapshot.json)。

本轮未运行真实知识库问答或改变知识库 generation/manifest，不报告生成答案准确率，也不把离线 SQLite 结果当作生产 MySQL 集成验收。修复时应把这些失败样本纳入正常回归，并针对时间清理与恢复行为补隔离的 Redis/MySQL 集成验证。
