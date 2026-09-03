# 新增格式 chunks 的 Embedding v3 执行授权

授权结论：`APPROVED:EMBEDDING_FORMAT_REMAINDER_459_MAX_46_RETRY_ZERO`

- 输入：扫描/图片 157 chunks + DOCX-PDF 302 chunks，共 459；
- DashScope batch size 10，最多 46 batches；
- 独立 checkpoint：`embedding-format-remainder-attempt-v3.json`；
- 自动重试 0；
- 禁止写入 Zilliz；
- 不修改或复用已完成的 Embedding v2 checkpoint。

