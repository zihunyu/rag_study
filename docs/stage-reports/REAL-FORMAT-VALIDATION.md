# 五类真实格式统一验证

状态：`REVIEW_REQUESTED_REAL_FORMAT_VALIDATION_COMPLETE`

范围：非 ASR 五类真实样本各 10 份，共 50/50。音频/ASR 与真实 7 天观察继续
`deferred_by_user`。本阶段只聚合既有本地与真实供应商证据，新增外部调用 0。

## 动态汇总结果

以下数值由匿名 evidence、checkpoint 与落盘节点动态计算并硬断言，不是手写常量替代结果。

| 格式 | 样本 | 执行链路 | Chunks | Locator expected/matched |
| --- | ---: | --- | ---: | ---: |
| 文本 PDF | 10 | 本地只读解析 | 25 | 10/10 |
| PPTX | 10 | 本地只读解析 | 385 | 25/25 |
| XLS/XLSX/CSV | 10 | 本地只读解析与严格 cell-range coverage | 259 | 13/13 |
| 扫描 PDF / 图片 | 10 | MinerU scan-v4 4份 + scan-v5 6份 | 157 | 10/10 |
| DOCX | 10 | LibreOffice匿名PDF + MinerU严格PDF locator | 302 | 20/20 |
| **总计** | **50** | 非 ASR 5×10 | **1,128** | **78/78** |

- scan combined：10/10、locator 10/10、artifacts 10、nodes/chunks 157；
- DOCX-PDF：10/10、locator 20/20、artifacts 10、nodes/chunks 302；
- 原生 DOCX content/recovery 结果保留为 content-only 与失败分析证据，不作为最终物理页码
  验收来源；
- 所有 50 份源样本处理前后 SHA-256 保持，`source_samples_modified=false`；
- 格式 Gate：`format_quality_ready=true`、`real_acceptance=true`。

匿名 JSON evidence：`artifacts/final-validation/real-format-validation.json`。

## Embedding 覆盖

- Embedding v2：669 chunks / 67 batches；
- Embedding v3：459 chunks / 46 batches，输入为 scan 157 + DOCX-PDF 302；
- 总覆盖：1,128 / 1,128 chunks，113 batches；未覆盖 0；
- 独立 v3 attempt：`embedding-real-attempt:v3-format-remainder`，checkpoint
  `embedding-format-remainder-attempt-v3.json`；
- v3 已完成 46/46、vectors 459、全部 1024 维 finite、chunk-ID 映射正确；
- automatic retries 0、Zilliz write 0；
- 不复用、不删除、不覆盖已完成的 Embedding v2 checkpoint，也不把 459 chunks 写入旧快照。

## UAT 与其余边界

- 冻结 pending 候选：78 条，SHA-256 验证且原文件字节级不变；
- 用户已批准全部 78 条；`approved.json` 中仅 status 改为 `APPROVED_BY_USER`，稳定 ID、
  question、locator 与 evidence 均不变；
- approval manifest 只含 pending/approved hash、count、decision 与 approved IDs hash，不含正文；
- `require_user_review_before_model_calls(approved)` 通过；Reranker/LLM requests 仍为 0；
- artifacts：`uat-candidates/pending-review.json`、`uat-candidates/approved.json`、
  `uat-candidates/approval-manifest.json`；
- audio/ASR：`deferred_by_user`；
- real 7-day observation：`deferred_by_user`；
- MySQL G3/G4 migration、外部 lifecycle drill、production-like performance/restore 与真实
  UAT 继续作为后续独立输入，不影响本次格式 Gate 通过。

## 完整性与工程证据

- 10 个历史 provider checkpoint SHA-256 全部匹配冻结值；
- secret scan 0 findings；未输出 Token、API Key、Base URL、签名 URL、原文件名或正文；
- Docker 使用 0；Git commit/merge/rebase/tag/push/PR 0；
- 本阶段新增外部调用 0；
- 完整质量门：279 passed、Ruff lint/format 272 files、mypy 102 source files、frontend、
  DOCX-PDF inputs、real-format evidence 与 secret scan 全部通过。

STAGE_REVIEW_REQUESTED:REAL_FORMAT_VALIDATION_COMPLETE
