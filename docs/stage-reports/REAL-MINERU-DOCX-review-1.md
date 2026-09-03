# 真实 MinerU DOCX 结果审核 1

审核结论：`BLOCKED:DOCX_CONTENT_SUCCESS_PAGE_LOCATOR_UNRELIABLE`

- recovery-v1 只查询/下载既有结果：2 requests，create/PUT 0，retry 0；
- 供应商结果成功落盘：1 artifact、27 nodes/chunks；
- 既有 expected pages 为 1、2，但 MinerU Office 的 27 个节点全部为 page 1；
- 原始 Office `content_list` 无 bbox、无 anchor；第 1 份 locator 仅匹配 1/2；
- DOCX v2 按门禁未执行，请求 0、checkpoint 不存在；
- 本机未发现 LibreOffice 或 Microsoft Word；
- scan 10/10、locator 10/10 与 Embedding 669/669 保持成功；
- 自动重试 0，无敏感泄漏、无提交、无 Docker。

## 需要决策

1. 推荐：安装 LibreOffice，本地 DOCX→PDF 后再走 MinerU，以保留物理页定位；
2. 或接受 DOCX 原生 MinerU 仅做内容/结构定位，修改既有 page locator 验收标准。

