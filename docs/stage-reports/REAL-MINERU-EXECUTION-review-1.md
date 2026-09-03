# 真实 MinerU 执行审核 1

审核结论：`BLOCKED:MINERU_SCAN_V2_HTTP_401_NO_RETRY`

## 独立核对

- scan v2 仅发出 1 次 create batch，请求返回 HTTP 401；
- checkpoint 为确定 `FAILED`，不是 UNKNOWN；provider code/category 不存在，trace 只保留哈希；
- completed/upload/poll/download/artifact 均为 0，自动重试与 Token failover 均为 0；
- scan v2 没有第二次运行；DOCX v1 没有启动、没有 checkpoint、请求数 0；
- 旧 MinerU checkpoint、旧 Embedding checkpoint及成功的 Embedding v2 checkpoint 未改变；
- Embedding v2 已完成 67/67 batches、669 vectors，全部 1024 维 finite，Zilliz 写入 0；
- 无 Token、URL、文件名、正文或响应正文泄漏；无提交、无 Docker。

## 下一步

- 用户需在 MinerU API 管理页面确认 Token 未过期、未撤销且具有 Precision API 权限；
  建议新建 Token 后只在 `config/.env` 更新原始值，不带 `Bearer ` 前缀；
- scan v2 失败 checkpoint 不得复用或删除；修正后使用新的 scan v3 attempt；
- 新 scan 重试需要用户明确授权；DOCX 既有授权尚未消耗，扫描成功后仍可继续使用。

