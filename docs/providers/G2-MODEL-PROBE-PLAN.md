# G2 Embedding / Reranker 单次探测计划（未执行）

状态：`MODEL_PROBES_PASSED`

运行以下命令只生成计划，不发起网络请求：

```powershell
& '.\.venv\Scripts\python.exe' scripts/plan_model_probes.py
```

## Embedding

- 计划请求数：1；输入数：1；
- 只允许固定的公开合成文本，不使用仓库正文、用户问题或业务数据；
- 验证响应数量、向量维度、有限数值、延迟和限流响应头；
- 不记录输入、向量、API Key 或 Endpoint 值。

## Reranker

- 计划请求数：1；文档数：2；
- 使用固定公开合成问题和文档；
- 验证索引唯一/范围、已知相关文档排序、延迟和限流响应头；
- 不记录正文、打分原文、API Key 或 Endpoint 值。

默认 HTTP transport 带计费保护；未显式批准时在网络调用前抛出
`BILLABLE_MODEL_CALL_APPROVAL_REQUIRED`。Mock transport 可执行契约测试且不联网。

## 实际执行

用户已批准各最多 5 次，本轮实际各 1 次、自动重试 0：

- Embedding：PASS，337.407 ms，1024 维，有限数值，范数 1.0000001；
- Reranker：FAIL，1093.307 ms，`HTTPStatusError`，安全码
  `MODEL_PROBE_HTTP_ERROR`，不可重试；
- 供应商未返回可安全用于估算的费用字段；
- 未输出 Base URL、API Key、请求正文或原始响应。

用户修正配置后执行一次 Reranker-only 探测：PASS，1579.984 ms，2 个索引唯一且范围
有效，已知相关文档第一。最终累计 Embedding 1/5、Reranker 2/5、自动重试 0；两项均停止调用。
