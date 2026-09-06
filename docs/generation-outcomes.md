# 生成结果与合法拒答

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
生成器 revision 增加 `structured-status:v2`，使生产证据包和答案缓存体现此次协议变更。
内部现有生成器构造 `DraftAnswer` 时默认状态为 answered；空草稿不会隐式变成拒答。
已有非空已回答缓存仍可解码；Redis 和内存答案缓存都拒绝保存拒答结果。
