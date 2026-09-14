**当前知识库后端与公开项目的功能对照审查（2026-09-14）**

结论：当前项目已经具备完整的文档问答主流程。更值得投入的是资料范围、表格语义、业务筛选、反馈处理和费用治理；仅增加检索次数、换框架或增加图数据库，不能保证回答更准确。本轮另外复现了两个长文总结分支的问题，应先修复。

审查范围：`E:\Data\codex\20260831rag` 的当前后端工作区，包含上一轮尚未提交的修复，不能只用 Git HEAD 代表被审查代码。公开检索重点覆盖 RAGFlow、Dify、Onyx、Haystack、LlamaIndex、LightRAG 和 Microsoft GraphRAG，检查官方文档及代表性源码；这是有针对性的公开项目对照，并非穷尽互联网上所有知识库实现。0727 未修改。

本轮只新增审查材料和隔离的构造资料；没有修改业务源码、调用付费模型、连接生产库做写入或重新向量化已有资料。下文“复现”指直接运行当前组件的本地构造案例，不代表真实模型端到端准确率。

**公开项目里有哪些值得借鉴的做法**

| 项目 | 本轮检查到的具体功能 | 对当前项目的借鉴价值 |
| --- | --- | --- |
| Dify | 手动、自动生成元数据过滤条件，并将限定文档范围传给检索；人工标注问答及命中记录 | 增加产品、地区、日期等明确筛选；为经过审核的常见问题维护标准回复。见[检索源码](https://github.com/langgenius/dify/blob/318964e4dc10ad836ac8ab88d280377b83f7e7a1/api/core/rag/retrieval/dataset_retrieval.py#L155)和[标注回复源码](https://github.com/langgenius/dify/blob/318964e4dc10ad836ac8ab88d280377b83f7e7a1/api/core/app/features/annotation_reply/annotation_reply.py#L18)。 |
| Onyx | 外部资料连接器、定期更新、源端移除资料的对账清理；部分连接器支持权限同步 | 把“导入一次”扩展为长期跟随资料源更新。权限同步在其文档中注明为企业版功能，不应把全部能力当作免费版现成组件。见[连接器说明](https://docs.onyx.app/admins/connectors/overview)和[对账清理源码](https://github.com/onyx-dot-app/onyx/blob/6e4b2a066459e3bd7217341b6f2fa5af4eb4bc4f/backend/onyx/background/celery/tasks/pruning/tasks.py#L481)。 |
| RAGFlow | 分块更新接口；RAPTOR 分层摘要构建，以及摘要和向量缓存 | 补齐文本分块人工纠错；为反复阅读的大文档建立可复用的章节导航。见[分块更新源码](https://github.com/infiniflow/ragflow/blob/185f089310fae3298ac5d4a639a0432f251bac5a/sdk/python/ragflow_sdk/modules/chunk.py#L60)和[摘要树源码](https://github.com/infiniflow/ragflow/blob/185f089310fae3298ac5d4a639a0432f251bac5a/rag/advanced_rag/knowlege_compile/raptor.py#L393)。 |
| Haystack | 按同一父节点下命中子块比例，递归合并上下文；分别评估检索和生成 | 当前已有父子块，可以借鉴按命中范围决定读多大段，而不是重新实现父子检索。见[自动合并源码](https://github.com/deepset-ai/haystack/blob/f7b46875f94d65c05c6d0527c8d160cf6be92de0/haystack/components/retrievers/auto_merging_retriever.py#L132)和[分阶段评估说明](https://docs.haystack.deepset.ai/docs/evaluation)。 |
| LlamaIndex | 根据问题选择 SQL 或向量检索，并可将结构化查询结果与文档证据组合 | 合计、数量、排名等问题先做确定性计算，再找文字解释。见[SQL 与向量组合源码](https://github.com/run-llama/llama_index/blob/7169bcd0dca2e16aecc8e0247f34e50079d9c0d5/llama-index-core/llama_index/core/query_engine/sql_vector_query_engine.py#L53)。 |
| LightRAG | 实体、关系和文本块联合检索，区分实体侧和关系侧查询 | 适用于需要串起多个文档中人物、产品、部件、组织关系的场景。见[查询与上下文构建源码](https://github.com/HKUDS/LightRAG/blob/f16a6415d1523ee1fe1ec9d7d1e406b017e7f3e0/lightrag/operate.py#L6075)。 |
| Microsoft GraphRAG | 面向实体的局部查询、面向资料整体的全局查询，以及逐步深入的查询 | 说明“找一个事实”和“总结整个资料集”可以采用不同流程。其官方文档明确说明全局查询较消耗资源。见[查询模式说明](https://microsoft.github.io/graphrag/query/overview/)。 |

这里借鉴的是可分离的功能设计。上游项目的功能说明、样例或公开分数，都不能直接证明在你的资料上效果更好。

**当前已经有的能力，应继续保留**

| 能力 | 已核对的当前实现 |
| --- | --- |
| 多格式解析 | 原生 Office、文本及表格解析，另有 MinerU 与视觉资料处理路径；不是只能读取 PDF。见[解析路由](E:/Data/codex/20260831rag/backend/src/ragkb/document_processing/parsers.py:40)。 |
| 分块 | token、结构、语义三种策略；表头、来源位置、父子上下文等已有实现。见[分块策略](E:/Data/codex/20260831rag/backend/src/ragkb/document_processing/chunking.py:49)。 |
| 节省重复入库费用 | 文档和问题向量持久缓存、批量向量化、内容去重、目录增量同步、Worker 复用统计。见[缓存适配](E:/Data/codex/20260831rag/backend/src/ragkb/adapters/cache_access.py:1)、[目录同步](E:/Data/codex/20260831rag/backend/src/ragkb/application/directory_sync.py:1)。 |
| 检索与回答 | 关键词与向量混合检索、重排、子问题证据保护、补充检索、最终来源选择。见[检索流程](E:/Data/codex/20260831rag/backend/src/ragkb/application/search.py:370)。 |
| 完整性与核验 | 必答项回执、遗漏部分补答、重新核验、冲突与数值检查。见[覆盖回执](E:/Data/codex/20260831rag/backend/src/ragkb/domain/question_coverage.py:120)、[补答](E:/Data/codex/20260831rag/backend/src/ragkb/application/aspect_repair.py:1)。 |
| 治理与验收 | 文档版本、审核发布、撤回、权限过滤及最终复核；验收案例、人工评审、从会话转案例、语义通过门槛。见[从会话生成案例](E:/Data/codex/20260831rag/backend/src/ragkb/api/routers/qa_acceptance.py:248)、[语义验收门槛](E:/Data/codex/20260831rag/backend/src/ragkb/evaluation/rag_quality.py:184)。 |

尤其不能再把“没有语义分块”“没有 Excel 支持”“没有父子检索”“没有问答评测”“没有缓存保留策略”列为整个项目缺失；问题在具体实现的边界。

**已有功能，但仍不足：七项主要发现**

| 编号 / 优先级 | 已有功能 | 不足与影响 | 证据强度 |
| --- | --- | --- | --- |
| I1 / 最高 | 章节阅读和缓存 | 章节结果已经返回后，缓存写入失败仍能打断本次总结 | 本地故障注入复现 |
| I2 / 最高 | 总结前选相关文档 | 文件名匹配可能过早收窄到主文档；读取报告仍显示范围完整 | 本地双文档复现 |
| I3 / 高 | 问题拆分与逐项检查 | 省略问法、混合并列项仍未形成足够细的要求清单 | 本地规划函数复现；未证明最终必然漏答 |
| I4 / 高 | Excel 导入与表格分块 | 标题可能被当成表头，公式进入知识库的是公式字符串 | 本地工作簿解析复现 |
| I5 / 高 | 目录增量同步 | 源文件移除只被报告，不会形成待撤回处理流程 | 当前调用路径确认 |
| I6 / 高 | 差评记录与验收平台 | 两者尚缺少反馈处理状态、责任人、案例关联和修复结果回写 | 路由、服务、持久化接口确认 |
| I7 / 高 | 问答预算与费用统计 | 有次数、token、时间上限，缺少按金额、用户或知识库累计的硬预算；当前单价均未配置 | 当前配置白名单读取与预算代码确认 |

I1：长文总结还留有一个缓存故障分支。

在[overview.py:374](E:/Data/codex/20260831rag/backend/src/ragkb/infrastructure/overview.py:374)，缓存读取、章节模型调用、缓存写入、结果使用处于同一个流程；异常处理只涵盖供应商响应异常。[VisualLedger.cache_put](E:/Data/codex/20260831rag/backend/src/ragkb/infrastructure/visual_ledger.py:148)直接执行 SQLite 写入，数据库异常会向外传播。缓存连接默认等待可达 20 秒。

复现中，构造的章节阅读器已返回有效原文引用，随后注入 `sqlite3.OperationalError`，`OverviewReader.read()` 没有返回本次结果。这里是上轮缓存修复遗漏的章节分支，不能用文档向量缓存、答案草稿缓存已经修好来覆盖它。建议缓存读失败按未命中处理；缓存写失败保留本次结果；辅助缓存采用短超时。版本审核状态等权威数据的读取不能一并忽略。

I2：选中了主文档，不等于找全了产品资料。

准备两份资料：`星云设备.md` 写保修三年，`星云设备申请材料.md` 写身份证和发票。问“总结星云设备的保修及申请材料”。[resolve_scope](E:/Data/codex/20260831rag/backend/src/ragkb/infrastructure/overview.py:67)只匹配到较短文件名，直接返回；用于补找相关文档的回调没有执行。复现返回的来源只有主文档，而 `scope_complete` 和读取阶段的 `complete` 都为 `true`。

根源是[读取报告](E:/Data/codex/20260831rag/backend/src/ragkb/infrastructure/overview.py:270)主要把“选中范围读完了”当作范围完整。建议文件名只作初始线索；逐项确认相关文档，再分别记录“范围已确认”“选中文档已读完”“问题已答完”。明确指定某一份文档时，才把用户指定范围作为完整性边界。此复现没有运行最终模型；后续核验仍可能发现资料不足，不能据此宣称最终一定错答。

I3：要求清单还依赖字面规则。

[question_aspects](E:/Data/codex/20260831rag/backend/src/ragkb/domain/question_coverage.py:12)已能拆开“保修多久，申请需要哪些材料”，但本轮得到：

| 问题 | 当前清单 | 还缺什么 |
| --- | --- | --- |
| A款支持哪些接口，B款呢？ | 1 项：整个问题 | B款继承“支持哪些接口”这一要求 |
| 介绍星云设备的优点、缺点和适用人群 | 2 项：优点；缺点和适用人群 | 三个独立检查项 |
| 比较A款和B款的价格和保修期限 | 2 项：两款价格；两款保修 | 可以进一步形成“产品 × 指标”的四格检查表 |

这不是说模型看不懂原问题，而是程序无法对每个小要求提供单独的证据与回答回执。建议先构建实体、指标、条件的要求清单；简单问法规整即可，含省略、指代或多产品多指标时才启用有预算上限的结构化规划。保留完整原问题和原始否定、时间条件。检索预算应按未覆盖要求分配，不能把“每项必须单独查一次”作为前提。

补充边界：[plan_queries](E:/Data/codex/20260831rag/backend/src/ragkb/application/query_planning.py:13)在上限 4 时最多对前三项产生专门焦点；但正常 QA 初始只用最多两次查询，并预留补查预算，见[evidence.py:235](E:/Data/codex/20260831rag/backend/src/ragkb/application/evidence.py:235)。因此不能把规划函数输出误报为整轮问答的实际检索次数，也没有再次发现之前的“4 变 12”问题。

I4：Excel“读出来”与“理解表格”之间还有缺口。

构造工作簿：第 1 行是合并标题，第 2 行是真正的产品、数量、单价、销售额表头，后两行销售额使用公式。当前[SpreadsheetParser](E:/Data/codex/20260831rag/backend/src/ragkb/document_processing/office_parsers.py:193)把第一条非空行作为全表表头；数据行的 `table_header` 因而是标题。由于 `data_only=False`，销售额为 `=B3*C3`，不是计算后的 200。

建议保留“表标题、单位、列名、行名、单元格地址、公式、已计算值”的对应关系；识别一个工作表中的多个表区与多行表头。没有可信计算值时明确标记未求值，不能让模型把公式当作已得到的金额。合计、同比、前十名等问题，应读取授权数据范围并确定性计算，结果回指参与计算的行。需要重处理的是受影响的表格资料，不是全部历史文档。

I5：增量同步目前没有覆盖资料移除后的治理。

[directory_sync.py:284](E:/Data/codex/20260831rag/backend/src/ragkb/application/directory_sync.py:284)计算 `removed_sources`，之后更新快照并返回列表；这里没有调用文档撤回或归档流程。若资料已发布，仅移除源文件不会使它自动退出可回答范围。随着下一次快照覆盖，移除事件也不应只依赖一次命令输出保存。

建议增加持久化的“源端缺失 / 等待复核”状态和对账任务；确认不是目录暂时不可达、移动或另一个别名仍在引用后，再按知识库设定撤回。复用已有生命周期、权限和缓存失效机制。借鉴 Onyx 的资料源对账思路，不能简单地见不到文件就物理删除。[上游实现](https://github.com/onyx-dot-app/onyx/blob/6e4b2a066459e3bd7217341b6f2fa5af4eb4bc4f/backend/onyx/background/celery/tasks/pruning/tasks.py#L689)。

I6：差评需要进入现有改进流程。

[QAService.feedback](E:/Data/codex/20260831rag/backend/src/ragkb/application/qa.py:1462)记录评分、原因、评论和版本身份；[仓储端口](E:/Data/codex/20260831rag/backend/src/ragkb/contracts/rag.py:123)没有对应的反馈处理与案例关联接口。目前已支持手动把会话转成验收案例，并明确不把机器当前回答当作标准答案，这一点应保留。

建议补齐“差评 → 分类待办 → 人工补充正确要求及来源 → 关联验收案例 → 修复后复查 → 关闭或重开”。可借鉴 Dify 的人工标注与命中记录，但不要因为语义相近就复用不同产品、年份或适用条件的标准答案。[标注回复源码](https://github.com/langgenius/dify/blob/318964e4dc10ad836ac8ab88d280377b83f7e7a1/api/core/app/features/annotation_reply/annotation_reply.py#L18)。

I7：目前的预算是资源预算，还不是人民币总账。

[Limits](E:/Data/codex/20260831rag/backend/src/ragkb/application/qa_budget.py:48)只有调用数、输入 token、输出 token、秒数。它有效约束单次任务的消耗，但不同模型单价不同，多人同时提问也不会自然形成每天总金额上限。本轮从当前配置只读取费用字段，10 个相关输入/输出单价全部为 0；这表示未配置价格，不能解释为真实免费。

建议增加独立费用表及其生效版本，金额缺失显示“未知”；在现有资源预算外，增加用户、知识库及全局日/月金额额度。请求发送前并发安全地预占，供应商用量返回后结算；未知结果暂占额度，待对账，避免同一余额被多个请求同时花掉。问答、OCR、重新入库都应进入同一累计口径。预算不足时保留可返回的已核验部分，不能省略核验来凑完整答案。

**当前缺少或尚未形成完整产品能力的功能**

“缺少”基于本轮检查的后端路由、契约及执行路径；相似底层类不等于已可配置、可使用的完整功能。

| 优先级 | 功能 | 当前边界与建议 | 是否需要全量重新向量化 |
| --- | --- | --- | --- |
| 高 | 按产品、地区、业务日期等严格筛选 | 已有租户、空间、文档 ID、权限过滤；没有通用的业务字段过滤协议。给文档维护结构化标签，先筛再检索；可参考 Dify。 | 通常不需要；回填元数据及对应索引即可，取决于最终存储设计 |
| 高 | 业务有效期与文档替代关系 | 已有版本和撤回机制；现有 `valid_from/to` 主要是授权投影时效，不能直接等同于“2025版政策适用哪一天”。补充业务生效期、替代关系和人工确认的来源优先级。 | 通常不需要 |
| 高 | 可核对的表格计算查询 | 已有表格提取和数值核验，尚无通用的表数据计算执行通道。用只读表查询/确定性计算处理数量、金额、排名，再与文档解释合并。可参考 LlamaIndex。 | 只处理目标表格及其结构化投影 |
| 高 | 文本分块纠错、停用与局部重建 | 已有分块查看、文档审核及图像结构人工修订；未找到普通文本块对应的纠错发布链路。增加修订记录、原文差异、审核和局部重建，保留来源真实性。可参考 RAGFlow 分块更新入口。 | 不需要；只重建修改块及受影响父块 |
| 中 | 企业术语、别名和型号词典 | 选择器提示词允许补查同义词，但没有可维护的企业术语映射。支持“内部简称 → 正式型号”，有歧义则澄清。 | 通常不需要 |
| 中，资料源较多时升高 | 外部连接器及源权限同步 | 当前上传和目录脚本可用；未发现 Wiki、网盘等资料源的统一轮询、游标、删除对账和权限映射接口。参考 Onyx、RAGFlow；先接最常用的一种资料源。 | 不需要重做现有库；新增/变更资料正常增量入库 |
| 中 | 可复用的文档与章节导航索引 | 已有父子块、章节阅读和章节缓存；章节缓存键包含完整问题，换问法仍可能重读。增加不依赖具体问题的章节提要/主题索引，按需构建；参考 Haystack 自动合并和 RAGFlow 摘要树。 | 原向量可保留；新增摘要会有额外模型与向量费用 |
| 按需 | 跨文档实体关系检索 | 当前图功能主要处理图片/图表中提取的结构及已明确的图间引用；尚未形成整个语料库的实体、关系、出处和增量维护索引。参考 LightRAG/GraphRAG。 | 原向量可保留；关系抽取与新索引成本可能较高 |
| 并发增长后 | 多实例、共享原文件和后台扩容 | 当前是明确的单实例部署边界，不是偷偷失效的 HA 实现。生产配置会拒绝多实例。扩展需要共享对象存储、分布式协调、统一发布/任务状态。 | 文件与状态可迁移；不应因此强制重算相同模型向量 |

业务过滤的本地依据：[SearchRequest](E:/Data/codex/20260831rag/backend/src/ragkb/api/models.py:193)、[AskRequest](E:/Data/codex/20260831rag/backend/src/ragkb/api/models.py:236)、[ReadingOptions](E:/Data/codex/20260831rag/backend/src/ragkb/application/reading_scope.py:14)、[SearchContext](E:/Data/codex/20260831rag/backend/src/ragkb/domain/retrieval.py:55)。虽然文档节点有通用 metadata，当前问答请求与检索上下文仍没有业务过滤表达式。

业务时间与优先级依据：[SecurityProjection](E:/Data/codex/20260831rag/backend/src/ragkb/domain/retrieval.py:15)、[在线检索使用当前时间](E:/Data/codex/20260831rag/backend/src/ragkb/api/routers/rag.py:103)、[证据 authority_rank 固定为 0 的说明](E:/Data/codex/20260831rag/backend/src/ragkb/application/evidence.py:285)。已有保守冲突处理，应继续保留；不能把重排分数当作政策优先级。

文本纠错依据：[分块只读接口](E:/Data/codex/20260831rag/backend/src/ragkb/api/routers/documents.py:185)。章节复用边界见[缓存键](E:/Data/codex/20260831rag/backend/src/ragkb/infrastructure/overview.py:367)；部署边界见[生产配置门禁](E:/Data/codex/20260831rag/backend/src/ragkb/config/report.py:75)。

**建议实施顺序，以及怎么验收**

| 顺序 | 工作 | 可检查的完成标准 |
| --- | --- | --- |
| 1 | 修补章节缓存故障、总结范围完整性 | 注入缓存读写故障仍保留已取得章节结果；双文档案例必须找到申请材料，或明确报告范围未确认，不能只读主文档便显示完整 |
| 2 | 业务元数据过滤、有效期与资料移除对账 | 产品/地区/日期不匹配资料不能占用召回名额；业务历史查询不绕过当前授权；移除事件可追踪，别名仍存在和源端不可达不会误撤回 |
| 3 | 问题要求矩阵、Excel 表区及计算通道 | “A款…B款呢”有独立要求；标题与表头区分；公式与计算值区分；合计结果能追溯参与计算的范围 |
| 4 | 反馈待办、验收关联、金额预算 | 差评能关联处理人和案例，修复后有复查结果；两个并发请求不能共同超支同一剩余额度；未知价格/用量不能作为零成本 |
| 5 | 按实际资料构成增加连接器、分块修订、章节导航 | 优先处理高频资料；已有向量继续使用，只对新增、修改或新增摘要付费 |
| 6 | 有跨文档关系问题和规模需求后再考虑图索引、分布式部署 | 有明确业务收益及成本上限后做局部试点，不以“技术更复杂”作为上线依据 |

以上顺序优先减少错用资料、漏读、漏检查和重复付费。新技术的效果需要相同问题、相同资料和可核对的标准答案才能比较；本轮没有给出准确率百分比。

**复核材料**

- [本地构造案例与结果](E:/Data/codex/20260831rag/artifacts/knowledge-benchmark-20260914/probe-results.json)
- [可重跑的组件探针](E:/Data/codex/20260831rag/artifacts/knowledge-benchmark-20260914/probe_current.py)：只在本次 artifacts 目录生成小型工作簿，不写业务数据库。
- [公开源码及当前源码摘要清单](E:/Data/codex/20260831rag/artifacts/knowledge-benchmark-20260914/source-manifest.json)

公开源码以链接中的提交固定；本地材料保留下载摘要。公开仓库文件只作阅读，没有执行外部项目代码。
