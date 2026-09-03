# MinerU 新 attempts 审批

审批结论：`APPROVED:MINERU_SCAN_V2_AND_DOCX_V1_EXECUTION_NO_COMMITS`

## 独立复核

- 完整测试 261 passed；Ruff lint、241 files format、mypy 99 source files 通过；
- Embedding v2 已完成 67/67 batches、669/669 vectors，全部 1024 维 finite；
  自动重试 0、Zilliz 写入 0；
- 旧 `mineru.json` 与 `embedding.json` SHA-256 保持不变；
- `mineru-scan-attempt-v2.json` 与 `mineru-docx-attempt-v1.json` 尚不存在；
- 两个固定入口分别限制扫描/图片 10 份和 DOCX 10 份，各自 max files 10、
  max requests 330、poll 30、interval 10 秒、retry 0、Token failover 0；
- 结果匿名原子落盘并重读规范化节点，分别复核 10 / 20 个 expected page locators；
- DOCX 使用匿名 `.docx` 文件名，新增 chunks 不加入已完成的 669 Embedding 范围。

## 执行顺序

- 先执行扫描/图片；成功后执行 DOCX；
- 扫描创建阶段失败时先停止，不继续触发共享 Token/Endpoint 的 DOCX 请求；
- 任一请求失败后禁止重跑对应 attempt。

