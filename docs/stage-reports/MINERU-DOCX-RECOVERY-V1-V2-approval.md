# MinerU DOCX recovery-v1 与 v2 本地执行器审批

审批结论：`APPROVED:MINERU_DOCX_RECOVERY_V1_AND_V2_READY_AWAITING_REAL_AUTHORIZATION`

- DOCX/Office 强制 `page_idx`，`bbox` 可选；扫描 PDF/图片仍强制 bbox；
- recovery-v1 只读取 v1 既有 batch，create/PUT 固定为 0，只允许 status/download；
- 原 DOCX v1 checkpoint 只读，恢复使用独立 checkpoint；
- DOCX v2 只处理 positions 2–10 共 9 份，不重传第 1 份；
- recovery 首份 expected locators 2，v2 expected locators 18，组合目标 20/20；
- retry 0，新增 chunks 不加入已完成的 669 Embedding；
- 独立定向审核 54 passed、Ruff、mypy 通过；开发完整质量门 270 passed；
- 两个新 checkpoint 当前不存在，本地修正新增网络调用 0。

原 DOCX v1 已因本地验证失败结束，既有授权不能自动扩展到新的恢复请求与 v2 attempt。

