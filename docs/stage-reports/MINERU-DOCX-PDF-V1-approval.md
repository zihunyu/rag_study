# MinerU DOCX→PDF v1 本地执行器审批

审批结论：`APPROVED:MINERU_DOCX_PDF_V1_READY_AWAITING_REAL_AUTHORIZATION`

- LibreOffice 26.8.0.3 已安装并验证 headless console launcher；
- 10 份 DOCX 已匿名本地转换为 10 份 PDF，源哈希均未改变；
- PDF 页数范围 2–3、总页数 25，existing expected page 覆盖 10/10；
- 转换器使用匿名 input.docx、隔离 UserInstallation、owned process/timeout、
  PDF magic/pypdf/页数/哈希校验与原子 manifest；
- 固定 `execute-docx-pdf-v1`，checkpoint 当前不存在；
- 范围 10 份派生 PDF、is_ocr false、max requests 330、locator 20、retry 0、failover 0；
- PDF 结果严格要求 page+bbox，新增 chunks 不加入 669；
- 原生 DOCX v1/recovery/v2 证据全部保留；
- 独立定向审核 58 passed、Ruff、mypy 通过；开发完整质量门 274 passed；
- 本地准备新增网络调用 0。

用户仅批准安装 LibreOffice，真实 DOCX→PDF MinerU attempt 仍需明确授权。

