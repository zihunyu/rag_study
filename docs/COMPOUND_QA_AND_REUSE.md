# 复杂问答覆盖与任务复用统计

本次仅修改当前项目后端。现有分块、向量和知识库连接继续使用，不需要重新入库。

## 1. 子问题独有证据进入重排候选池

各子问题的结果先经过权限、有效版本和向量写入批次校验，再去重并分配重排名额。约一半的候选名额按子问题轮流分配，优先保留仅由一个检索任务命中的材料，其余名额按融合排名补齐，总量仍受 `RETRIEVAL_RERANK_TOP_K` 限制。

例如多个问题都命中 40 条常见保修材料，但只有“需要哪些凭证”命中“需要提供购买发票”，发票材料会获得保留名额。重排后的非前排材料仍进入证据选择与冲突核验池，不会仅因最终展示数量限制被丢弃。容量小于独立证据数量时仍不可能保留全部材料，因此最终覆盖检查仍然必要。

## 2. 多问题批处理

生产 Milvus/Zilliz 适配器支持 `search_bm25_many` 和 `search_dense_many`，问题向量接口为 `embed_queries`。问题向量独立生成，原始问题和条件始终保留；不会把多个问题拼成一个向量。

一次包含两个问题的冷检索，在输入条数和 token 均未超过 Embedding 批次限制时：

| 接口 | 之前 | 现在 |
| --- | ---: | ---: |
| Embedding HTTP 请求 | 2 | 1 |
| BM25 Milvus 请求 | 2 | 1 |
| Dense Milvus 请求 | 2 | 1 |

Embedding 仍先查持久缓存，只提交缺失输入，并继续遵守每批条数、每条 token 和每批 token 上限。返回向量按 Embedding 响应序号复原；Milvus 结果组数量不匹配时拒绝使用，防止正文与向量、问题与结果错配。

两个检索通道并行，单个通道暂时不可用时保留另一个通道；失败的批量请求不会自动拆成多次请求放大费用。不支持批处理的适配器保留原有单条路径。

**批量只减少接口往返，不减少底层检索任务的预算计数。** 两个问题仍消耗两个查询名额，提交批量请求前一次性校验名额。原有简单/标准/深入预算及生成、核验预留保持有效。

以上是离线调用计数验证，不是线上响应时间或准确率基准。

## 3. 必答项覆盖检查

后端从完整问题提取必答项并赋予 `A1`、`A2` 等稳定编号；列表独立于检索次数预算。最多列出 32 项，超出部分保留在最后一个复合项中，不静默丢弃。

证据选择的现有模型请求返回 `aspect_sources`，将必答项关联到已授权、已选择的 `E#` 证据。生成请求携带相同清单。独立答案核验的现有请求返回 `aspect_checks`，将每项关联到 `E#`、`C#` 和最终答案中的原文片段，不新增一轮覆盖检查请求，但清单和回执会增加少量输入、输出 token。

覆盖回执位于问答响应及持久化结果的 `coverage_report.required_aspects`：

| `answer_status` | 含义 |
| --- | --- |
| `answered` | 本项有支持证据，且最终答案已经回答 |
| `partial` | 回答了有依据的部分，仍有缺项 |
| `evidence_missing` | 本轮提供的证据不足；不代表整个知识库不存在资料 |
| `answer_missing` | 已找到相关证据，但答案没有回答该项 |
| `ambiguous` | 对象或条件仍有歧义 |
| `unchecked` | 缺少有效核验回执，或回执不再适用于最终显示的答案 |

每项包含 `aspect_id`、`question`、`evidence_status`、`evidence_ids`、`answer_status`、`claim_ids`、`answer_quote`；整体包含 `answered`、`total` 和 `complete`。普通读者只能看到实际引用的证据编号。

“已回答”要求引用有效、对应事实已独立核验为支持、摘录确实出现在最终答案中。生成修复后的答案使用最新回执，显示文本改变时旧摘录失效。复杂问题存在漏项或未核验项时，回答范围标为 `partial` 并附带 `REQUIRED_ASPECTS_INCOMPLETE`；已核验的独立事实仍可返回。关键前提缺失仍遵守原来的阻断规则。

旧适配器未返回回执时显示 `unchecked`，不会把命中关键词或出现引用当作已回答。资料撤回后，历史答案和覆盖报告中的答案摘录一同隐藏。规则拆分与模型语义检查都不能保证完全识别所有隐含子问题，实际准确度仍需用业务题集评测。

## 4. 按任务持久统计，包含 Worker

账本为 `<LOCAL_STORAGE_ROOT>/artifacts/reuse-ledger.sqlite`。API、会话和 Worker 使用同一个 SQLite WAL 文件；进程重启后仍可读取。备份应采用一致的 SQLite 备份方式，并纳入该文件。

统计使用任务上下文，不通过相减全局计数器计算；并发问答不会串账。会话问题改写与后续问答共享任务编号。Worker 按入库 `job_id` 汇总，失败、重试分别保留 attempt 与状态。每次缓存检查、每批成功生成和每次 HTTP 派发/返回分别持久记录，后续任务失败不会清零已完成的部分。

查询接口：

- `GET /api/ingestion-jobs/{job_id}/reuse-statistics`：要求本租户对应文档的管理权限。队列清理了已完成任务后，仍可凭持久记录和当前文档权限读取统计。
- `GET /api/rag-runs/{rag_run_id}/reuse-statistics`：只允许本租户该问答的原提问用户读取。
- 普通问答与会话的 `coverage_report.reuse_statistics` 同时包含当次统计快照。

| 字段 | 含义 |
| --- | --- |
| `available` | 是否有统计记录；上线前的历史任务没有回填，不能解释为零费用 |
| `requested_vectors` | 调用方请求的向量数量，含重复输入及重试 |
| `cache_reused_vectors` | 直接由持久缓存满足的输入数量 |
| `duplicate_reused_vectors` | 当批缺失输入经内容去重后省下的重复生成数量，不与缓存命中重复计数 |
| `missing_vectors` | 各次调用中缺失的不同输入数量之和，失败重试时可能再次累计 |
| `generated_vectors` | 已成功生成并校验的向量数量 |
| `embedding_batches` | 已成功完成的 Embedding 批次数 |
| `http_attempts` | 实际进入 HTTP 发送边界且已返回或抛出异常的请求数，含失败和重试，含其他模型请求 |
| `embedding_http_attempts` | 其中 Embedding 的 HTTP 尝试数 |
| `failed_http_attempts` | HTTP 异常或非 2xx 的次数；不包括后续业务内容校验失败 |
| `unknown_http_outcomes` | 已登记派发但未收到回执，例如进程中断；可能仍在执行，不能视为已成功或未收费 |
| `input_tokens` / `output_tokens` | 服务商已明确返回的 token 用量之和 |
| `unknown_usage_attempts` | 未返回完整用量的请求数，不能把缺失用量当作零 |
| `attempt_count` / `attempts` | 尝试次数及各次状态、开始和结束时间 |

缓存省下的向量数量为 `cache_reused_vectors + duplicate_reused_vectors`。成功批次数和 HTTP 尝试数并不等价：一次批次可因限流而发送多次 HTTP 请求。账本是运行记录，不是服务商账单；未知派发结果及缺失用量必须保留为未知。

## 验证方式

新增专项回归见 `backend/tests/test_compound_qa_improvements.py`，包括独有发票证据保留、授权过滤、批量顺序、原子预算、通道降级、核验漏项与伪造回执、答案修复、缓存回执更新、并发统计隔离、失败重试、HTTP 用量未知、Worker 持久记录、跨租户和跨用户访问拒绝、撤回资料后的摘录清理。

测试使用临时存储、合成资料和模拟服务，不调用付费模型，不重建实际知识库索引。

2026-09-11 验证结果：全量后端回归 1825 passed、7 skipped、9 deselected；补充的覆盖/图文专项回归 57 passed。Ruff、280 个源文件的 mypy 检查、两份 OpenAPI 快照校验通过。后端和 Worker 已重启并通过就绪与新鲜心跳检查，持久账本已建立。线上 OpenAPI 需要登录，未把匿名访问返回的 401 当作服务故障。
