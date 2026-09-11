**当前知识库后端深度审查 — 2026-09-11**

实施更新：用户选定的 R1–R8 已完成修复、回归并在当前后端和 Worker 生效，当前实现、验证和部署结果见 [八项修复说明](E:/Data/codex/20260831rag/docs/AUDIT_FIXES_20260911.md)。以下保留原始审查时的事实、源码指纹和失败探针；其中“未修改源码”等描述仅指审查阶段。R9、R10 不在本次修复范围。

审查对象为 `E:\Data\codex\20260831rag` 的当前后端，重点覆盖入库复用、分块、向量索引、问题规划、检索、证据选择、生成核验、预算、统计和评测。本轮没有修改应用源码、生产配置或真实知识库，也没有重启服务、调用付费模型。未操作 0727 项目及其数据库。

核心判断：现有权限、版本发布、引用定位、独立主张核验和复用能力值得保留；主要缺口集中在多个阶段之间的合同不一致。一个阶段保护了证据，下一阶段可能再次截断；核验已经发现未检查，输出状态仍可能显示完整；统计原本是辅助能力，却能中断成功的模型请求。扩展知识库规模和复杂题能力之前，应先修这些边界。

这不是对真实问答准确率的测定。离线复现证明特定输入或故障条件下的代码行为，不代表线上发生比例；没有大库压测，也没有当前版本与 0727 的同题、同资料、同预算 A/B 测试。

**验证范围与结果**

| 验证 | 本轮结果 | 应如何理解 |
|---|---|---|
| 已有复杂问答、预算、向量复用、质量指标专项回归 | 102 passed | 已覆盖行为仍正常，不等于没有其他边界缺陷 |
| 新增审查探针 | 9 项：7 个期望行为断言失败，2 个能力边界观察通过 | 两个截断探针对应同一类缺陷；不能把 7 个断言机械理解为 7 个独立根因 |
| 上一轮 5 个原始问题复测 | 3 failed，2 passed | 检索总预算、重排前独有证据保护已修好；同步复用、过期上传恢复、超长语义分块仍未修 |
| 后端源码指纹 | 与上一轮实施完成时一致 | 本轮应用源码没有变化；历史未提交修改完整保留 |

源码 SHA-256：`99cfb7ce8ca4718995e49b0c943cdd970b1339d746553fa23011bbb7e81d3fb8`。源码树包含 280 个 Python 文件；这一计数不表示逐行穷尽全部文件。审查依据为关键路径阅读、定向搜索和下述离线故障注入。

验证材料：[审查回执](E:/Data/codex/20260831rag/artifacts/deep-backend-review-20260911/audit-receipt.json)、[新增复现代码](E:/Data/codex/20260831rag/artifacts/deep-backend-review-20260911/test_deep_review_repro.py)、[新增探针结果](E:/Data/codex/20260831rag/artifacts/deep-backend-review-20260911/repro.log)、[已有回归结果](E:/Data/codex/20260831rag/artifacts/deep-backend-review-20260911/baseline.log)、[旧问题复测](E:/Data/codex/20260831rag/artifacts/deep-backend-review-20260911/previous-findings.log)。失败探针保留在正常测试目录之外，断言的是期望行为，未伪装成修复完成。

**优先处理的问题**

| 编号 | 优先级 | 问题 | 证据级别 | 主要影响 |
|---|---|---|---|---|
| R1 | P1 | 必答证据在后续截断中丢失，覆盖状态没有随之变化 | 两处离线复现 | 漏答、错误判断无需补充资料 |
| R2 | P1 | 统计写入失败覆盖模型成功响应，并跳过问答预算结算 | HTTP 故障注入复现 | 已付费结果丢失、重试、统计和预算不准 |
| R3 | P1 | 必答项回执可缺省，未核验的复合问题仍能标记完整 | 使用真实核验适配器、模拟响应复现 | 用户误以为所有要求都回答了 |
| R4 | P1 | 已成功任务被队列清理后，未变化文档失去增量复用资格 | 旧问题再次复现 | 重复解析、建版本和写索引 |
| R5 | P1，换模型前必须处理 | 索引没有绑定完整 Embedding 模型合同 | 静态追踪及模拟 schema 检查 | 同维度模型变更可能静默降低召回质量 |
| R6 | P2 | 问题向量已经命中缓存，仍可能等待无关入库锁超时 | 跨线程锁冲突复现 | 重复问题变慢或失败 |
| R7 | P2，复杂题能力边界 | 证据选择最多 8 条，独立于文本长度和配置 | 9 条短证据复现 | 多对象比较、长清单覆盖不足 |
| R8 | P2；准确率验收前必修 | 重复结果使 NDCG 超过 1；词面和引用指标无法识别事实错配 | 数值复现 | 评测结果失真、错误判断改动收益 |
| R9 | P2 | 上传意图过期后无法正常续建 | 旧问题再次复现 | 批量同步中断后的恢复失败 |
| R10 | P2，启用 semantic 前处理 | 语义分块先嵌入超长原始段落；评分器内存缓存无界 | 长段落复现＋静态检查 | 切换语义模式后报错、长任务内存增长 |

P1 表示应进入下一批修复，并不表示每次普通问答都会触发。R5 的风险条件是模型合同变化；R10 的超长段落问题不等同于当前 structure 模式有相同故障。

**R1：子问题保护尚未贯穿完整问答链路。**

第一处在模型证据选择器输入处。它按现有顺序累加候选，超过本地估算的 16,000 token 就跳过整条；没有再次按必答项分配预算。探针提供 E1–E8 共八条较长材料，再提供含“需要提供购买发票”的 E9，模型实际只收到 E1–E8。即使检索和重排候选保护已经成功，选择模型仍可能根本看不到这条材料。

第二处在选择后的生成证据装配。选择器已经选出 E1–E8，并将凭证要求映射到 E8；前七条累计占用约 7,350 个本地估算 token，发票材料加入后超过 8,000，E8 被改为 `conflict_context`。生成输入只剩 E1–E7，但包中的 `coverage=sufficient`、`complete=true`、A2→E8 的 supported 状态全部保留。复现时检索预算还剩 2 次，停止原因已经是 sufficient。

后续核验仍可看到完整证据池，条件修复也可能救回特定遗漏，因此这里不能推断“所有这样的请求最终必然漏答”。已证实的是截断没有维护覆盖状态，正常生成阶段确实丢掉了已选中的必要材料。

建议将证据选择、文本裁切和覆盖报告统一交给同一个装配步骤：先为每个必答项保留最小可用证据，再补相关背景；以段落、表格行等可定位单元裁切；裁切后重新计算覆盖。预算不足时明确记录待回答项，不能继续沿用截断前的 sufficient。所有摘要和裁切仍需能回到原文验证。

代码：[模型输入截断](E:/Data/codex/20260831rag/backend/src/ragkb/adapters/evidence_selection.py:56)、[选后 8,000 上限](E:/Data/codex/20260831rag/backend/src/ragkb/application/evidence.py:468)、[覆盖结果组装](E:/Data/codex/20260831rag/backend/src/ragkb/application/evidence.py:512)、[实际生成证据过滤](E:/Data/codex/20260831rag/backend/src/ragkb/domain/rag.py:127)。数据：[第一处截断](E:/Data/codex/20260831rag/artifacts/deep-backend-review-20260911/selector_input_truncation.json)、[第二处截断](E:/Data/codex/20260831rag/artifacts/deep-backend-review-20260911/post_selection_truncation.json)。

**R2：辅助统计成为问答的故障传播点。**

模拟模型 HTTP 已返回 200、实际输入 usage 为 2 token，然后让任务统计完成事件抛出 SQLite `database is locked`。调用方最终收到该数据库异常，拿不到成功响应；问答预算仍保留请求前预估的 880 input token。原因是 `finally` 中先执行统计写入，再执行预算 settle，统计异常会打断后面的操作。

SQLite 锁冲突、写盘失败都可能到达该路径；本轮仅注入异常，未测量线上发生率。任务开始和结束记录也直接传播账本异常，存在相同的关键路径耦合。

应把“是否允许花费”的预算准入与“记录花了多少”的可观测统计分开。预算准入失败可以拒绝发起请求；供应商结果已返回后，应确保预算结算独立执行，统计故障降级为显式 unavailable/pending，并通过有界队列、独立写入器或可靠事件补偿重试。不能把同一个成功请求因统计失败再次发给供应商。

代码：[响应后的 finally](E:/Data/codex/20260831rag/backend/src/ragkb/adapters/model_http.py:203)、[任务统计上下文](E:/Data/codex/20260831rag/backend/src/ragkb/application/reuse_statistics.py:44)、[SQLite 同步事件写入](E:/Data/codex/20260831rag/backend/src/ragkb/adapters/reuse_ledger.py:67)。[复现数据](E:/Data/codex/20260831rag/artifacts/deep-backend-review-20260911/stats_failure_discards_response.json)。

**R3：必答项核验有兼容性缺口。**

问题“请给出设备的保修期限和申请材料”被本地规则解析为一个复合 A1。材料池同时包含保修期限和购买发票；生成器只回答保修期限。模拟核验模型正确支持这条保修主张，但省略新加的 `aspect_checks`，当前协议允许该响应通过。

最终结果为 `verified=true`、`answer_scope=answered`、外层 `complete=true`、无警告；内层 `required_aspects.complete=false`，A1 的回答状态却是 unchecked。探针使用当前 `OpenAICompatibleClaimVerifier` 接收模拟模型响应，证明缺省回执确实能进入真实适配器路径。它没有测定供应商多大概率省略该字段。

单个复合 A1 本身不一定有错，模型可以完整审查复合要求；真正的问题是必答项核验缺失时仍给予“完整”状态。应在当前协议版本强制完整回执，对历史结果明确标记未核验，并统一外层、内层的完成语义。补强中文并列要求识别后，仍需让模型或规则验证“是否覆盖原问题全部要求”，避免拆分错误成为新的盲区。

目前 `answer_missing` 主要进入最终覆盖报告，未见以它为驱动的通用补答调度闭环。已有引用、表达、条件修复应保留；建议新增一次有预算的定向补答，保留已核验事实，仅补确实遗漏的部分，并重新核验新增内容与整体条件。

代码：[本地拆分规则](E:/Data/codex/20260831rag/backend/src/ragkb/domain/question_coverage.py:12)、[缺省回执兼容](E:/Data/codex/20260831rag/backend/src/ragkb/domain/question_coverage.py:75)、[外层状态门槛](E:/Data/codex/20260831rag/backend/src/ragkb/application/qa.py:514)、[选择回执同样可选](E:/Data/codex/20260831rag/backend/src/ragkb/adapters/evidence_selection.py:215)。[复现数据](E:/Data/codex/20260831rag/artifacts/deep-backend-review-20260911/legacy_aspect_gap.json)。

**R4、R9：增量同步仍依赖短寿命任务记录和旧上传意图。**

再次模拟已成功入库的任务记录被清理，未变化文件在同步中被视为 updated，同一文档版本数从 1 变成 2。当前 `_live()` 在文档、内容和版本均匹配后，仍要求旧队列任务存在；生产 Redis 对成功任务的保留期为 7 天。向量缓存可能继续节省 Embedding 费用，但重复解析、建版本、索引写入仍有开销，不能视为成功的增量同步。

另一个恢复探针在上传中断后使会话过期，再运行同步，仍收到 `UPLOAD_SESSION_EXPIRED`。同步层继续复用旧 operation，上传层正确拒绝过期会话；应在同步恢复层续建未完成意图，保留已完成操作的幂等保护。

需要建立独立于队列的“处理成果记录”：内容哈希、解析器版本、分块版本、向量合同、成果地址、发布状态。队列负责执行和重试，成果记录负责判断是否已经做完。新的队列任务可以指向既有成果，不应反向依赖旧任务永久存在。

代码：[旧任务消失即不可复用](E:/Data/codex/20260831rag/backend/src/ragkb/application/directory_sync.py:121)、[Redis 保留期](E:/Data/codex/20260831rag/backend/src/ragkb/adapters/redis_queue.py:109)、[上传 operation 复用](E:/Data/codex/20260831rag/backend/src/ragkb/application/directory_sync.py:242)、[会话过期校验](E:/Data/codex/20260831rag/backend/src/ragkb/application/uploads.py:72)。[本轮复测结果](E:/Data/codex/20260831rag/artifacts/deep-backend-review-20260911/previous-findings.log)。

**R5：Embedding 缓存隔离不能替代索引合同。**

缓存已经按模型等信息区分，但写入索引的记录没有 Embedding 模型/版本合同，检索过滤也没有该条件。只读 schema 检查验证维度、字段及 BM25 配置；模拟切换两个不同名字的 1,024 维模型，schema 检查均为 compatible。这一结果符合 schema 检查当前职责，也说明它不能证明语义空间兼容。

同维度模型生成的向量不因此可混用。应在 generation 元数据中保存 provider/model、版本或人工受控 revision、维度、归一化方式、文档和查询输入模板；查询前与活跃索引合同核对。历史库缺少元数据时，依据可核查的历史配置补登记，不能根据维度猜测。更换模型时可并行建立新 generation，保持旧 generation 可服务，再通过对照评测切换。

增加合同检查不需要全量重新向量化；真正改用不兼容的向量模型时，才需要为目标资料生成对应向量。单纯调整重排、回答和覆盖策略通常可以复用现有向量。

代码：[索引记录](E:/Data/codex/20260831rag/backend/src/ragkb/adapters/vector_indexing.py:279)、[检索过滤](E:/Data/codex/20260831rag/backend/src/ragkb/adapters/zilliz.py:88)、[schema 判断](E:/Data/codex/20260831rag/backend/src/ragkb/adapters/zilliz.py:212)。[模拟检查](E:/Data/codex/20260831rag/artifacts/deep-backend-review-20260911/embedding_contract_not_checked.json)。

**R6：缓存的锁粒度抵消了部分查询复用价值。**

当前 `_embed()` 先锁住整批输入对应的锁条带，再读 SQLite。锁文件只有 4,096 个条带，不同文本可以落在同一个条带。探针预写问题向量，另一个线程持有不同文本的相同条带；读取问题缓存仍报 `EMBEDDING_CACHE_WAIT_TIMEOUT`，供应商调用数为 0，数据库中的缓存值一直有效。

探针用很短的超时稳定复现；生产超时更长时表现可能是等待，不能直接引用探针时长预测线上延迟。锁也会跨后续 HTTP 批次持有。应先批量读取缓存，仅锁缺失项，进入锁后再次读取防止重复计算，并按实际批次尽快释放。前台查询与后台入库还应有独立并发份额。

代码：[先锁再读](E:/Data/codex/20260831rag/backend/src/ragkb/adapters/model_http.py:623)、[锁条带实现](E:/Data/codex/20260831rag/backend/src/ragkb/adapters/embedding_cache.py:37)。[复现数据](E:/Data/codex/20260831rag/artifacts/deep-backend-review-20260911/warm_cache_stripe_blocking.json)。

**R7：复杂问题的宽度被固定常量限制。**

九种产品分别询问保修期限，九条独立证据总共只有 90 个汉字，但选择器返回九个合法 ID 会报 `EVIDENCE_SELECTION_INVALID`。输入 token 很充足，仍受最多八条 ID 的协议约束；仅提高 `final_evidence_count` 不能解除该上限。当前必答项列表则最多能表示 32 项。

这是多来源覆盖能力边界，不能推断所有九个子问题都失败：若一条材料覆盖多项，八条也可能足够。建议以“覆盖要求所需的最小证据集合＋实际 token/费用预算”为依据；无法在一次生成内覆盖时，分批形成带来源的核验结果，再汇总答案，同时显式报告未完成项。无需默认给每个子问题各调用一次大模型。

代码：[八条硬限制](E:/Data/codex/20260831rag/backend/src/ragkb/adapters/evidence_selection.py:173)、[必答项数量](E:/Data/codex/20260831rag/backend/src/ragkb/domain/question_coverage.py:42)。[复现数据](E:/Data/codex/20260831rag/artifacts/deep-backend-review-20260911/fixed_eight_sources.json)。

**R8：需要把工程回归与真实回答质量分开评估。**

检索指标实现有一个具体错误：相关集合为 `{E1}`，排名为八个重复 E1，NDCG@8 得到 **3.9534645161**。DCG 重复累加相同结果，理想 DCG 却只按唯一相关结果计算。应先规范排名中的唯一身份或明确拒绝重复输入，并校验指标范围。正常搜索路径有去重措施；此探针证明评测入口不够健壮，不证明当前线上排名普遍重复。

生成指标还有能力盲区。标准答案“甲产品保修一年，乙产品保修三年”，错误答案把一年、三年对调，`answer_token_f1=1.0`；两者使用相同来源 ID 时，citation precision 和 recall 也都是 1.0。词面 F1 本身就是词项重合率，问题在于用它替代事实正确性判断。引用 ID 命中同样不等于引用支持该句话。运行时另有主张、数字和语义核验，不能因这些评测盲区声称运行时必然放行该错误答案。

已有真实验收和签名 Gold 合同，应继续使用；但低成本 Gold 路径固定 20 个分块、默认 10 个问题，只适合作为小规模门槛。9 月 9 日的记录也明确说明：24 题经过跨版本修复复测，代理复核未等同于独立人工签署。本轮新功能仍缺少同一最终版本、固定资料上的完整真实问答对照数据。

建议保留词面指标作为诊断，新增按实体—属性—值—单位—适用条件判定的事实正确率、必答项完整率、引用蕴含率、错误拒答率、无依据回答率，并一起记录 P50/P95 延迟、模型调用和金额。按普通事实、比较、否定、例外条件、长表、历史版本、跨文档关系、权限、资料不足分桶。调优集和隐藏验收集分开，失败样本补入回归，业务人员复核高影响争议案例。

代码：[DCG 累加](E:/Data/codex/20260831rag/backend/src/ragkb/evaluation/rag_quality.py:54)、[文本 F1](E:/Data/codex/20260831rag/backend/src/ragkb/evaluation/rag_quality.py:72)、[引用指标](E:/Data/codex/20260831rag/backend/src/ragkb/evaluation/rag_quality.py:96)、[小规模 Gold 合同](E:/Data/codex/20260831rag/backend/src/ragkb/evaluation/real_gold.py:36)。证据：[NDCG 结果](E:/Data/codex/20260831rag/artifacts/deep-backend-review-20260911/duplicate_ndcg.json)、[事实错配指标](E:/Data/codex/20260831rag/artifacts/deep-backend-review-20260911/semantic_metric_blind_spot.json)、[历史真实验收边界](E:/Data/codex/20260831rag/docs/QA_FINAL_ACCEPTANCE_20260909.md:9)。

**R10：大资料量还存在分块和内存边界。**

9000 token 的单段文本能由结构分块处理；语义分块在生成小块前，先让评分器向量化整个段落，在 Embedding 输入上限 8192 时失败。应先切成有位置映射的安全窗口，再判断语义边界，保留原始内容完整性。评分器 `_cache` 长期保存文本及向量，未见容量限制或逐文档释放。

另一个静态性能风险在索引写入：虽然 Embedding HTTP 已分批，索引器会把整份文档的向量积累到 `vectors`，再构建整份 `records` 后写入。内存占用仍随文档分块数和向量维度增长。本轮没有内存压测，不报告崩溃阈值；建议构建分批持久化的暂存成果，最终用现有 saga/fence 和发布机制原子切换，避免半成品可检索。

精确答案缓存命中前还要扫描整代分块投影、计算内容摘要并校验已验证图片字节。安全性有明确目的，但大库重复提问仍可能受数据库、磁盘开销限制。应研究事务维护的内容、权限、发布版本摘要，并覆盖定时生效/失效边界；不能通过删除这些安全检查换取速度。

代码：[语义预加载](E:/Data/codex/20260831rag/backend/src/ragkb/document_processing/chunking.py:435)、[评分器缓存](E:/Data/codex/20260831rag/backend/src/ragkb/document_processing/chunking.py:553)、[整文档向量累积](E:/Data/codex/20260831rag/backend/src/ragkb/adapters/vector_indexing.py:259)、[整库快照](E:/Data/codex/20260831rag/backend/src/ragkb/infrastructure/qa_snapshot.py:44)。

**不受当前框架限制，值得补齐的功能**

| 能力 | 当前实际基础与缺口 | 建议的目标行为 |
|---|---|---|
| 结构化问题任务 | 已有保留原条件的本地拆分；缺少通用实体、必答输出、前置条件、依赖关系合同 | “比较甲乙的保修及收费”变成明确的比较矩阵；缺失比较对象时不推导总体结论；明确独立可回答项 |
| 多种资料检索与执行 | 已有 BM25、向量、章节阅读、视觉及已审核图结构查询；通用问题主路径仍以文本证据为主 | 标识符走精确匹配；表格统计走源行执行；概述走章节阅读；关系问题走有来源的关系查询；语义问题走向量与全文组合 |
| 通用表格计算 | 已有数值事实检查、表格引用和有限显式算式核验；缺少完整的筛选、去重、分组、聚合执行合同 | 输出总额或差额时保存输入行、筛选条件、单位、算式和结果；处理跨页重复表头、合计行双算、单位换算 |
| 适用范围与规则优先关系 | 已有权限与生效期；索引的 product_ids、region_codes、applicable_versions 等为空，证据 authority_rank 无治理依据 | 产品、地区、版本、例外条款结构化；有经治理确认的替代/优先规则时应用规则；没有依据时明确冲突 |
| 历史时点问答 | 当前普通检索使用请求当前时间及 current_version/SERVING 过滤 | 显式支持“截至某日”和版本对比；历史业务有效时间与当前读取权限分别校验，不恢复已撤销权限 |
| 分阶段入库复用 | 已有内容去重和向量缓存；解析、分块、向量、投影成果尚需更完整的独立版本关系 | 每层有内容身份与处理合同；改投影只重写索引，改回答策略直接复用分块；资料未变时无需依赖旧队列任务 |
| 面向费用的调度 | 已有次数、token、时间总预算及阶段预留；已配置各角色价格并有用量记录 | 增加按模型价格的金额准入、知识库/租户日配额、批量入库预估；前台问答与 Worker 有保底并发；按缺失证据价值分配下一步预算 |
| 覆盖驱动的补答 | 已有引用、表达、条件修复和必答回执；缺少通用的遗漏项补答状态机 | 区分“未检索”“已检索但资料不足”“证据有、回答漏了”“已核验”；只修复对应缺口；预算不足返回已核验部分 |
| 可比较的业务验收 | 已有测试、Gold、真实验收记录；仍缺当前最终版本的大库隐藏题对照 | 同资料、同配置、同预算比较版本；结果同时展示正确性、完整性、成本、延迟，避免只优化某个均值 |

上述是功能演进建议，不代表这些能力全部需要新框架。已有图能力主要围绕经审核的图示结构，不能笼统说项目“完全没有图检索”；已有数字核验也不等于完整的数据分析执行器。参考：[现有图示查询](E:/Data/codex/20260831rag/backend/src/ragkb/infrastructure/graph_evidence.py:31)、[有限算式核验](E:/Data/codex/20260831rag/backend/src/ragkb/domain/numeric_facts.py:505)、[空业务元数据](E:/Data/codex/20260831rag/backend/src/ragkb/adapters/vector_indexing.py:300)、[当前时间检索范围](E:/Data/codex/20260831rag/backend/src/ragkb/application/evidence.py:95)、[现有预算模型](E:/Data/codex/20260831rag/backend/src/ragkb/application/qa_budget.py:49)。

**建议的系统边界与实施顺序**

可以保留现有部署，用清楚的内部接口拆出以下职责，先作为同一后端中的模块运行。没有证据要求立即改成多个微服务。

| 模块 | 保存/负责什么 | 核心不变量 |
|---|---|---|
| 内容与成果目录 | 原文、解析产物、块、向量合同、索引投影、成果引用 | 成果身份独立于短期任务，变更只失效相关处理层 |
| 问题任务与预算调度 | 必答项、对象、条件、依赖、阶段、次数/token/时间/金额 | 每个后续动作消耗同一个总预算；预算不足保留已完成项 |
| 证据目录与装配 | 证据来源、定位、适用范围、ACL、所属必答项、裁切记录 | 检索、重排、裁切、生成使用一致的证据身份；裁切后重新算覆盖 |
| 检索与确定性执行 | 全文、向量、章节、表格、关系查询 | 派生结果有输入来源和执行过程；不能冒充原文 |
| 答案合成与核验 | 必答项到事实、事实到来源、最终答案位置的回执 | 支持事实与完整回答分别判定；缺省检查不能升级为完成 |
| 统计与评测 | 任务消耗、缓存命中、故障、真实业务效果 | 统计故障不覆盖成功响应；验收数据绑定代码、模型、资料版本 |

第一批处理 R1、R2、R3、R4、R9，并补 R5 的合同检查。这一批主要修正状态、异常隔离和持久化判断，通常无需重新向量化现有资料。把本轮失败探针转成正常回归，并补隔离环境中的 Redis 清理、MySQL 恢复、SQLite 锁故障集成验证。

第二批处理缓存读取和锁范围、证据数量/文本预算装配、NDCG 与业务事实评测。在固定题集上比较修改前后“正确且完整”的回答比例与费用，防止单纯放大候选造成等待和核验成本增加。

第三批补问题任务合同、通用表格执行和有预算的补答。优先覆盖真实常见题型；只有实际关系问题需要时扩展有来源的关系索引，避免对全部文档预先执行昂贵实体抽取。表格缺少结构的资料可以定向重解析，能够复用既有向量的仍继续复用。

第四批在大资料量压测结果支持下，做流式入库成果、批量缓存读写、事务维护快照和多实例共享存储。当前向量缓存明确按单实例主机共享设计，统计 SQLite 和本地文件也绑定 storage.root；横向扩容前需要决定权威存储、缓存共享、事件一致性和备份恢复边界。

增加分块缓存、调整编排、替换重排器、补覆盖检查和修统计都不自动要求全库重新向量化。只有文本输入合同、分块结果或 Embedding 模型真正变化时，才计算受影响范围与新增费用。可以先修当前流程再评估框架替换；更换框架或向量数据库的收益应由同资料、同预算对照结果证明。
