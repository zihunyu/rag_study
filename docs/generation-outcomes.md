# 生成结果与合法拒答

## 问题判断：FLOW-01

默认 `SearchBackedEvidenceProvider` 在检索前调用 `QuestionAssessmentPort`，判断独立问题
是否能够进入知识库问答。Local 使用保守规则；Production 默认使用结构化模型判断器，
共享生成模型配置、连接池、并发限制、整体请求 deadline 和真实调用开关。此步骤只发送问题，
不发送文档内容；Production 每次问答多一次模型判断调用，包括可能命中答案缓存的请求。

| 输入情况 | disposition / reason_code | API status |
| --- | --- | --- |
| “它的保修期是多久？”；当前服务没有对话历史，无法确定“它” | needs_clarification / missing_context | needs_clarification |
| “请帮我预订明天的机票”；需要实际执行外部操作 | out_of_scope / unsupported_operation | out_of_scope |
| “产品 A 的保修期是多久？”、“如何预订机票？” | answerable / standalone_question | 继续检索、生成与验证 |

这里的范围指知识库证据问答的服务能力，并非按知识库标题猜测允许的主题。
陌生主题、没有命中、资料没有覆盖问题均不能据此判定为 out_of_scope。
模型判断器也可将纯创作或聊天请求标为 out_of_scope / outside_knowledge_qa。
Local 规则只识别明确代办请求及缺少指代对象的提问，未匹配的问题继续正常检索。
这些测试验证链路和协议；未进行真实模型的意图识别准确率评估。

需要澄清的结构化判断如下：

```json
{
  "disposition": "needs_clarification",
  "reason_code": "missing_context",
  "clarification_fields": ["subject"]
}
```

`clarification_fields` 只允许不重复的 subject、product、version、region、time_period，
且仅 needs_clarification 必须包含至少一项；状态和原因码也必须匹配。任何额外字段、
非法组合、损坏 JSON 都是 `QUESTION_ASSESSOR_PROTOCOL_INVALID`，不可重试；暂时故障为
`QUESTION_ASSESSOR_UNAVAILABLE`，可重试。两者均返回 system_error、verified=false，
不会伪装成澄清、拒答或检索故障。

两种合法提前结束状态均不检索、不生成事实答案、不调用声明验证器、不签发引用、不写答案缓存。
JSON 和 SSE API 都返回 answer=null、citations=[]、clarification_fields，并在 warnings
中保留原因码；审计证据包保存判断器 revision、原因和缺失字段，index_generation_id 为
not_retrieved。verified=true 只表示通过结构化业务状态检查，不表示完成了事实验证或检索。
前端用固定模板展示补充信息和服务范围提示，用户补全问题后重新提交即可正常检索。

旧证据包和结果缺少这些新字段时仍可读取。API 新增的 clarification_fields 默认为空数组。

## 生成阶段的证据不足

模型生成 JSON 必须包含 `status`、`answer`、`claims` 和 `citation_ids`。
`status` 只能为 `answered` 或 `insufficient_evidence`。

合法拒答使用以下完整结构：

```json
{
  "status": "insufficient_evidence",
  "answer": "",
  "claims": [],
  "citation_ids": []
}
```

已检索到的资料可能只覆盖产品配置，不能支持用户询问的退款政策。这时模型可以
返回上述拒答。问答服务在结构检查和最新权限复核通过后返回
`status=insufficient_evidence`、`answer=null`、`citations=[]`、`verified=true`，
并记录 `MODEL_INSUFFICIENT_EVIDENCE`。这里的 verified 表示合法的无答案业务结果
通过检查，不表示已经证明知识库中不存在相关资料。

拒答没有事实声明，因此不调用声明验证器，不签发引用，也不写入答案缓存。
检索健康状态与降级原因继续保留。请求再次执行时重新检索和生成。

`answered` 必须有非空答案、引用和 claims；每条 claim 必须有非空文本和字符串
证据 ID。上层继续检查引用是否真实存在，并进行声明验证和最终权限复核。

| 输出情况 | 业务结果或错误 |
| --- | --- |
| 显式合法拒答 | insufficient_evidence / MODEL_INSUFFICIENT_EVIDENCE |
| 缺失或未知 status、空响应、损坏 JSON、拒答中夹带正文或引用 | system_error / GENERATION_PROTOCOL_INVALID |
| answered 引用了不存在的证据 | system_error / CITATION_VALIDATION_FAILED |
| 模型服务暂时不可用 | system_error / GENERATION_UNAVAILABLE_AUTHORIZED_EVIDENCE_ONLY，retryable=true |
| 拒答生成期间权限失效 | system_error / FINAL_PERMISSION_RECHECK_FAILED，无答案 |

模型适配器不再接受缺失 status 的旧 JSON，包括看似完整的非空答案。
生成器 revision 增加 `structured-status`，使生产证据包和答案缓存体现此次协议变更。
内部现有生成器构造 `DraftAnswer` 时默认状态为 answered；空草稿不会隐式变成拒答。
已有非空已回答缓存仍可解码；Redis 和内存答案缓存都拒绝保存拒答结果。
