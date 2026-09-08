# 单知识库跨文件问答检测

检测日期：2026-09-08。

## 结论

**同一知识库能够跨多个文件检索和组装证据；本次真实三文件问答未通过完整验收。**
实际请求已将 Markdown、CSV、Word 三份文件的内容送入答案生成，但在独立核验阶段返回
`VERIFIER_CONDITION_WITNESS_INVALID`，最终没有发布回答或引用。不能仅凭检索成功或离线测试
通过宣称当前真实问答已完全可用。

## 真实页面检测

- 知识库：`重设计验收库-20260906`，三份文件均显示“可问答”，内容均为合成验收资料。
- 阅读方式：`自动判断`；资料范围：`当前知识库`。
- 测试会话：`fd7d6359e7d2c1c4c0cb3f801d3387643cf6`。
- RAG Run：`01a07fb8-7e4e-7003-ac5a-2f5c85dc23d1`。

问题：

> 请分别回答：星云 X1 的保修期及起算时间是什么？海岚 Q7 在常温、低温下的额定功率分别是多少？星桥服务合同取消后多久退款、退到哪里？请给出每项的资料引用。

持久化的同一份证据包包含以下独立文件，三份文件也都出现在最终的生成证据中：

| 文件 | 检索到的内容 | 生成证据示例 |
| --- | --- | --- |
| 星云X1-合成验收手册.md | 保修三年，从购买之日起计算 | E1 |
| 设备参数-合成.csv | 海岚 Q7 常温 420 W、低温 390 W | E10，包含表头及两行数据的父块 |
| 星桥服务合同-合成.docx | 取消服务后三十天内退款，原路返回支付账户 | E2 |

结果：

- 检索健康状态：`healthy`；证据选择器覆盖判断：`sufficient`。
- 原始证据共 14 条，来自 3 份文件；生成证据也覆盖这 3 份文件。
- 生成模型 revision 指向 `gpt-5.5`，独立核验模型 revision 指向 `gpt-5.4-mini`。
- 最终状态：`system_error`；`verified=false`；没有返回答案或引用。
- 错误：`CLAIM_VERIFIER_PROTOCOL_INVALID`、`VERIFIER_CONDITION_WITNESS_INVALID`。
- `real_acceptance=false`，本次检查没有生成或签署全局真实验收凭据。

错误定位到 `backend/src/ragkb/domain/answer_conditions.py` 的
`validate_condition_checks()`：该处验证核验模型给出的条件状态、答案摘录及证据引用是否满足
协议要求。当前持久化结果没有保留失败的原始核验响应，尚不能确定具体是哪条条件不符合要求，
也不能确定是模型响应不合规还是条件识别/匹配规则过严。后续应针对该请求保留脱敏的生成及核验
响应后定位，不能通过关闭条件核验来认定问题已解决。

之后尝试补测双文件问答及“跨图 / 多文档综合”模式时，新建会话返回 HTTP 500，随后
`127.0.0.1:8000` 和 `127.0.0.1:5173` 均无法建立连接。服务不可用的原因未确定；这两项现场
补测未完成，未计入通过项。

本地证据摘要：`artifacts/cross-document-qa/live-results.json`。

## 离线回归

新增 `backend/tests/test_cross_document_qa.py`，从临时知识库创建、上传、解析、分片、索引、
复核发布到 `/api/search`、`/api/ask` 和签名来源访问走实际本地链路。

| 用例 | 结果 |
| --- | --- |
| 显式选择同库两个文件，答案包含两个文件中的独立事实，引用分别回到对应文件 | 通过 |
| 使用整个知识库范围，答案及引用覆盖三个文件 | 通过 |
| 其他知识库和本库未发布文件不进入检索、证据或回答；显式选择它们也不能越过限制 | 通过 |

测试中的生成器是按实际检索证据摘录的测试替身，Embedding 和 Reranker 沿用本地模式的
确定性实现。检索索引、文件处理、权限、来源签名和事实核验未替换为虚构结果。这组测试用于
检验链路及隔离，不能代替真实模型的多文件综合能力验收，也未评估复杂跨文件多跳推理。

验证命令：

```powershell
.\.venv\Scripts\python.exe -X utf8 -m ruff check backend/tests/test_cross_document_qa.py
.\.venv\Scripts\python.exe -X utf8 -m pytest backend/tests/test_cross_document_qa.py backend/tests/test_knowledge_base_api.py backend/tests/test_local_real_rag_e2e.py backend/tests/test_hybrid_search.py backend/tests/test_answer_synthesis.py -q --tb=short
```

结果：Ruff 通过；**39 passed**。有一条现有 Starlette TestClient 关于 httpx 的弃用提示。

## 实现边界

- `application/evidence.py` 将所选 `space_id` 传入统一检索上下文，逐条收集不同
  `document_id` 的命中；默认不限制为某一个文件。
- `application/search.py` 按文件和章节分别计数，当前配置每次最终命中上限为 8 块，
  每个文件最多 3 块、每个章节最多 2 块；父块和补查可能增加证据包条数。
- `application/evidence.py` 在证据不足时允许一轮、最多两个补充查询，仍使用同一知识库和权限。
- `infrastructure/overview.py` 支持遍历所选知识库的多个有效文件；综合模式仍受读取预算限制，
  其现场效果本次未验证。
- 跨文件问答表示根据相关证据组织答案，不代表每次问题都读取或引用知识库全部文件。

本次只新增检测测试和报告，没有修改业务实现或核验规则。
