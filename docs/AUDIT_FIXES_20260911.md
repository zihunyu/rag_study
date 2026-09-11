**后端深度审查八项修复说明**

对应 [深度审查报告](E:/Data/codex/20260831rag/docs/DEEP_BACKEND_AUDIT_20260911.md) 中用户选定的八项。应用继续使用已有原文、分块和向量；代码修复不会启动全库重新向量化。原始审查探针及失败结果保留，新的回归测试位于 [test_deep_audit_fixes.py](E:/Data/codex/20260831rag/backend/tests/test_deep_audit_fixes.py)。

| 修复项 | 新行为 |
|---|---|
| 后续证据丢失 | 保留证据的检索任务来源；选择器输入优先给不同检索任务和 review 材料机会；生成装配优先安排必答项证据。整条材料装不下时记录遗漏 ID，重新计算覆盖，返回 EVIDENCE_PACKING_INCOMPLETE |
| 统计故障打断问答 | 已返回的 HTTP 响应先结算问答预算；任务统计开始、事件、读取、结束及全局用量写入的存储异常单独降级。任务统计和全局用量写锁等待均限制为 0.2 秒。当前任务统计返回 unavailable，能够保存的失败标记持久记录。身份冲突仍拒绝，不能通过统计降级混合不同租户任务 |
| 必答项回执缺失 | 无论一个还是多个要求，缺少覆盖检查都不会显示完整回答；保留已验证事实，但 answer_scope=partial，并提示 REQUIRED_ASPECTS_INCOMPLETE。补充识别“请给出设备的保修期限和申请材料”等属性并列要求 |
| 旧任务被清理导致重入库 | 当队列任务已不存在时，查询持久化的处理状态、质量报告和索引候选完成信息，校验内容、版本及水位，判定是否已完成；已失败的处理不会伪装为成功复用 |
| Embedding 模型身份 | 生产 Milvus/Zilliz 适配器通过 MySQL 登记表绑定目标集合、generation 与模型合同；新空 generation 在首次向量化前登记；不匹配的查询和索引写入在模型调用前拒绝 |
| 命中缓存仍等锁 | 批量读取缓存后，只锁缺失项；锁内再次读取，避免并发重复付费；按实际批次释放锁。未设置新模型 revision 时，原缓存命名空间保持一致 |
| 固定八条来源上限 | 最大选择数为配置值与必答项数量的较大者，安全上限 64；仍受选择器 16,000、生成输入 8,000 的本地 token 估算预算约束。九条短来源不再因八条协议限制报错 |
| 评测分数误代表准确率 | 检索排名按唯一 ID 计算 NDCG；词面/引用重合指标继续保留为诊断，独立报告语义评估。数字对象错配直接不通过；不能自动确认的改写标为 unreviewed，不允许仅凭 F1 通过验收 |

证据装配使用完整来源，不通过删掉条件、截断句子等方式凑预算。检索阶段的文字匹配只用于分配候选位置，不是事实支持的证明。若要求对应的整个证据组装不下，最终覆盖报告会降级；这不表示整个知识库没有相关资料。

必答回执的历史兼容策略是显式未核验，而不是将所有旧响应变成系统异常。因此，verified=true 表示保留的事实通过既有核验，完整程度需查看 required_aspects 和 answer_scope。这两者不能混为同一个准确率。

统计账本与费用准入预算职责分开。账本故障不使一个已成功的模型请求重发；未知的统计不能当作零费用。恢复期间若存储持续不可用，统计可能保持 unavailable 或未完成状态，系统不会伪造完整回执。MySQL 模型合同则是检索兼容性控制，读取失败时不会按“统计故障”放行。

**现有索引的模型合同登记**

新增表 `embedding_generation_contracts` 为追加式元数据；不改 Milvus 集合 schema 或向量值。绑定含目标 URI/数据库/集合的摘要、generation、Embedding endpoint 摘要、模型、受控模型 revision、维度、归一化设置、输入/输出合同。API key 不进入身份，不因密钥轮换使向量失效。

已登记 generation 不允许覆盖为另一份合同；模型、输入合同发生变化时，应使用新 generation，并明确安排相关资料的向量迁移。新参数 `EMBEDDING_MODEL_REVISION` 用于供应商模型版本或人工控制的升级标识；默认留空可兼容现有配置。供应商如果在相同名称后静默替换模型，系统不能只凭向量数组识别该变化，应由受控 revision 和回归基准管理。

对于已有向量但缺少合同的库，系统返回 `EMBEDDING_CONTRACT_UNREGISTERED`。升级工具分成预览和登记，必须提交已核对的合同摘要及来源说明：

```powershell
.venv/Scripts/python.exe scripts/bind_embedding_contract.py --generation YOUR_GENERATION
.venv/Scripts/python.exe scripts/bind_embedding_contract.py --generation YOUR_GENERATION --apply --contract-sha256 REVIEWED_DIGEST --provenance "升级前配置或历史入库记录的核对依据" --output artifacts/embedding-contract-registration.json
```

工具只针对当前项目配置的数据库执行追加式迁移及合同登记，没有 Embedding 调用、向量修改或删除。登记保留 `operator_attested_historical_configuration` 来源和 `historical_vectors_individually_verified=false`，不能把历史配置登记说成每条旧向量都重新验证过，更不能仅按维度登记一个未知模型。

**评测合同的变化**

`evaluate_quality()` 返回的 `metric_gate_passed` 仅表示旧数值指标达到门槛。总 `passed` 还要求 `semantic_assessment.passed`。每条案例的语义状态为 passed、failed、unreviewed，并带判定依据；非空准确率分数不再掩盖未评审的情况。低成本真实验收的逐题门槛也采用同一规则。

自动判断包括：无答案金标的拒答检查、与金标答案及来源集合完全一致的检查、数字与对象/单位关系的失配检查。它不是通用语义裁判。无法据此确认的释义、改写，需要可信业务评审，并将回执绑定到对应问题、标准答案、实际答案及引用集合：

```python
from ragkb.evaluation.rag_quality import semantic_review_digest

case["semantic_review"] = {
    "digest": semantic_review_digest(case),
    "reviewer_id": "实际评审人员标识",
    "reviewed_at": "实际评审时间",
    "correct": True,
    "complete": True,
    "citations_supported": True,
}
```

上述布尔值必须来自实际评审，不应为通过门槛自动填入。答案或引用变化后，旧 digest 不再有效。已有 Gold 数据集审核及签名规则继续适用；本轮没有替业务人员生成真实问答的审核结论，也没有新增付费准确率测评。

本批实现不包含原审查表中未被选定的上传会话续建、超长语义段落分块、完整表格执行器等后续工作。

**验证与上线结果**

| 检查 | 结果 |
|---|---|
| 最终后端全量回归 | 1,853 passed、7 skipped、9 deselected，0 failed；503 秒。跳过项为需显式启用的本机服务或隔离 Redis 测试；未执行付费/外部集成组 |
| 定向回归 | 286 passed、1 skipped；包含新增 28 个边界案例，与全量测试有重叠，不累计为另一批独立覆盖 |
| 代码及类型检查 | Ruff 通过；Mypy 检查 282 个后端源码文件通过 |
| MySQL 迁移 | 只新增 `embedding_generation_contracts`，45 个既有迁移跳过；登记当前 `production-g3-generation`，不覆盖旧合同 |
| 当前服务 | 已重启后端和 Worker，ready 检查及重启后的 Worker 心跳通过；向量 schema 和模型合同兼容检查通过 |
| 资料与费用 | 部署前后检索投影均为 7,272 条；队列仍为 47 SUCCEEDED、46 FAILED_FINAL，无进行中问答；未全库重新向量化，未调用付费模型 |

现有合同依据是当前已部署配置及历史模型/维度探针的一致性核对，登记 `text-embedding-v4`、1,024 维、归一化开启。没有宣称历史向量逐条验证完成，也没有据此给出真实问答准确率。本轮仅针对用户选定的当前项目实施修复，没有操作 0727。

最终后端源码 SHA-256：`dd005918708d75f236cbed4f2583ac57f5657820040d99d1aba4bd5a084a3948`。既有未提交修改保留，本轮未创建 Git 提交。

验证材料：[总回执](E:/Data/codex/20260831rag/artifacts/audit-fixes-20260911/verification.json)、[全量测试](E:/Data/codex/20260831rag/artifacts/audit-fixes-20260911/full.log)、[定向测试](E:/Data/codex/20260831rag/artifacts/audit-fixes-20260911/focused.log)、[部署后检查](E:/Data/codex/20260831rag/artifacts/audit-fixes-20260911/postcheck.json)、[模型合同登记](E:/Data/codex/20260831rag/artifacts/audit-fixes-20260911/contract-registration.json)。初次回归的失败日志另存为 `full-initial.log`，未被最终结果覆盖。
