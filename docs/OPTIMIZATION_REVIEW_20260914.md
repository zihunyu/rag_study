**当前后端可继续优化的地方 — 2026-09-14**

本轮按用户要求检查源码和配置，不进行真实业务问答、付费模型测试或大库压测。使用临时隔离数据和假模型响应确认局部行为；未修改应用代码、生产配置或生产数据库，未重启服务。下列结论不代表线上发生概率，也没有给出未经测量的速度或准确率提升比例。

当前 Git 提交为 `e08e62d1214b1f71faadf35009225b1b7feec2b7`，后端源码 SHA-256 为 `dd005918708d75f236cbed4f2583ac57f5657820040d99d1aba4bd5a084a3948`。这与上一轮修复回执中的源码指纹一致；此前的未提交修改现已由已有提交承载，本轮没有创建提交。

| 编号 | 优先级 | 通俗描述 | 核对依据 | 改善方向 |
|---|---|---|---|---|
| O1 | 最高 | 已经算好的结果，可能因保存缓存失败而拿不到 | 隔离故障注入：两次向量响应成功，均因缓存写入失败而向调用方抛异常；相同问题再次请求又计算了一次 | 保存缓存失败时保留本次已得到的结果，标明缓存异常；缓存读取故障另行决定是否按预算补算 |
| O2 | 高 | 同样问两件事，换一种说法就可能没有拆开检查 | “这款产品保修多久，申请需要哪些材料？”只识别为一个要求；“请给出设备的保修期限和申请材料”识别为两个 | 改善自然问法和主体继承；可在现有证据选择调用中补全问题清单，检查新增清单忠于原问题，避免另加一轮模型调用 |
| O3 | 高 | 明知答案漏了一项，目前主要是提示“不完整” | 隔离完整问答流程：资料含发票要求，核验明确返回第二项 answer_missing，最终只答三年，生成仅一次；完整状态已正确降级 | 有预算且证据已齐时，只补遗漏部分，保留已验证事实，再检查合并后的答案；资料不足时继续明确提示 |
| O4 | 高 | 让系统总结某个产品时，可能先读了别的资料 | 未传 document_ids 的章节阅读按文档列表顺序展开；缩小读取上限的隔离案例中，无关资料先占满额度，目标文档未入选，覆盖被正确标为不完整 | 先确定相关文档，再逐章读；整库综述应作为明确范围，由用户请求或已有范围规则决定 |
| O5 | 高，费用控制 | 当前能限制做几次、读多少内容、等多久，不能直接限定花多少钱 | 问答预算只有 calls/input_tokens/output_tokens/seconds；当前 Embedding、重排、生成、核验的每百万 token 单价均为 0 | 先按实际供应商账单口径补齐价格和币种，再做每问/每天/知识库金额预留与结算；未知单价显示“无法估算”，不能当免费 |
| O6 | 中，等待时间 | 同一份模型身份信息一次找资料会重复核对 | 普通单问题双路检索共触发 3 次 registry.require；生产 require 每次读取登记表 | 在一次请求内复用已核对的同一目标、generation 和完整模型身份；配置或代次变化重新核对，存储故障仍拒绝不兼容检索 |
| O7 | 中，大批资料同步 | 文件没变也会逐个重新读取文件、查询处理状态 | scan 每次读取全部支持文件计算哈希；每项 _live 分别查询版本、文档、版本列表、所属知识库和任务，旧任务缺失时再查完成记录 | 做变更发现和批量状态读取；必须保留完整内容校验及定期复核，不能只凭文件大小/修改时间断言内容没变 |
| O8 | 中，长期运行 | 缓存和统计明细会不断积累 | 向量缓存表没有容量、访问时间或过期字段；任务统计事件没有清理/汇总接口 | 问题缓存设容量与保留规则，文档向量缓存优先保留；旧统计先汇总再按保留规则清理，保留必要追溯关系 |

O1、O2、O3、O4、O6 有隔离执行结果；O5、O7、O8 来自当前配置和代码路径。O2 证明“逐项检查的粒度不足”，并不证明这句话实际问答必然漏答。O4 使用一条的边界上限复现文档顺序问题，当前配置上限为 1,200 块，不能将该探针说成真实大库压测。O8 未证明当前磁盘已满。

**代码位置与实现注意事项**

- O1：[向量结果先计算、后写缓存](E:/Data/codex/20260831rag/backend/src/ragkb/adapters/model_http.py:700)，[缓存读取](E:/Data/codex/20260831rag/backend/src/ragkb/adapters/model_http.py:634)。上一轮已隔离的是统计故障，这里是另一条缓存存储路径。不得把模型身份登记、权限检查等权威校验也当作可忽略缓存。
- O2：[问题拆分](E:/Data/codex/20260831rag/backend/src/ragkb/domain/question_coverage.py:12)，[保留原问题的检索任务](E:/Data/codex/20260831rag/backend/src/ragkb/application/query_planning.py:14)。保留原问题这一保护仍有效，缺的是独立必答项枚举。
- O3：[当前修复分支](E:/Data/codex/20260831rag/backend/src/ragkb/application/qa.py:1057)，[最终覆盖降级](E:/Data/codex/20260831rag/backend/src/ragkb/application/qa.py:505)。现有引用、表达和条件修复仍保留；本建议是新增针对漏答项的修复触发条件。补答不能偷换条件、引入无来源结论或越过总预算。
- O4：[自动识别概览问题](E:/Data/codex/20260831rag/backend/src/ragkb/application/reading_scope.py:28)，[按文档列表展开](E:/Data/codex/20260831rag/backend/src/ragkb/infrastructure/overview.py:137)。指定文档时已经有过滤；不足集中在未指定文档的默认范围。
- O5：[当前预算结构](E:/Data/codex/20260831rag/backend/src/ragkb/application/qa_budget.py:49)。API 与 Worker 已有共享的次数/内容速率控制和前后台排队机制，不应描述为“完全没有费用或并发控制”；新增的是金额准入能力。
- O6：[搜索前核对](E:/Data/codex/20260831rag/backend/src/ragkb/application/search.py:387)，[关键词路径](E:/Data/codex/20260831rag/backend/src/ragkb/adapters/zilliz.py:323)，[向量路径](E:/Data/codex/20260831rag/backend/src/ragkb/adapters/zilliz.py:351)，[登记表读取](E:/Data/codex/20260831rag/backend/src/ragkb/adapters/embedding_contracts.py:63)。本项减少的是数据库往返，不是模型 token 费用。
- O7：[文件全量校验](E:/Data/codex/20260831rag/backend/src/ragkb/application/directory_sync.py:53)，[逐项查询](E:/Data/codex/20260831rag/backend/src/ragkb/application/directory_sync.py:104)。当前 MySQL 已使用按需读取，未发现“每次加载全部文档状态”的问题；不把这项已有优化重复列为缺陷。
- O8：[缓存表及写入](E:/Data/codex/20260831rag/backend/src/ragkb/adapters/embedding_cache.py:31)，[任务统计事件](E:/Data/codex/20260831rag/backend/src/ragkb/adapters/reuse_ledger.py:19)。缓存与统计是不同用途，不能用统一过期时间粗暴删除。

**已排除的重复问题，以及先前保留项**

核验输入已对多条事实重复引用同一正文做共享来源去重。隔离构造 12 条事实引用一段正文，正文在实际请求中出现 2 次：共享事实来源一次、全量冲突池一次，并非按 12 条事实各复制一份。因此，“每条事实重复发送整段正文”不列为新缺陷。[现有去重](E:/Data/codex/20260831rag/backend/src/ragkb/adapters/model_http.py:2094)

上一轮修复的关键证据保护、缺省覆盖回执降级、统计故障隔离、任务清理后持久复用、缓存命中绕过锁、可扩大来源数量和评测纠偏均保留。本轮没有证据声称这些修复失效。

原审查的 R9“上传意图过期后恢复”和 R10“语义分块先处理超长原段落、内存缓存无界”也仍需处理：[上传续建路径](E:/Data/codex/20260831rag/backend/src/ragkb/application/directory_sync.py:237)、[语义分块评分缓存](E:/Data/codex/20260831rag/backend/src/ragkb/document_processing/chunking.py:548)。当前配置为 structure 分块，不能把 R10 写成当前普通入库必然失败；它是切换 semantic 前的待修项。本轮未重新执行这两个旧探针。

**实施顺序建议**

先做 O1，避免已算好的结果因缓存保存失败而丢失；随后做 O2/O3/O4，完善问题清单、漏项补答和文档范围；再做 O5/O6/O7/O8，完善费用上限和运行效率。大规模目录导入前同时处理 R9。上述方案通常可以复用已有分块和向量，不需要重建全库，也不要求更换当前框架。

验证材料：[隔离检查代码](E:/Data/codex/20260831rag/artifacts/optimization-review-20260914/inspect_boundaries.py)、[结果记录](E:/Data/codex/20260831rag/artifacts/optimization-review-20260914/observations.json)。其中假模型调用仅用于检查代码行为，没有访问模型供应商；本轮没有重跑全量测试，因为未修改应用源码。
