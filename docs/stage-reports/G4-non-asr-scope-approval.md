# G4 非 ASR 范围重基线审核批准

结论：`APPROVED:G4_NON_ASR_SCOPE_REBASELINE_NO_COMMITS`

审核时间：2026-09-01T13:45:55+08:00

## 独立证据

- `APP_SECRET_KEY` configured=true、Secret 类型正确、长度至少 32；审核未输出值；
- G4 配置：`gate_ready=true`、0 blocker、`current_validation_scope=non_asr`、`asr_scope_enabled=false`；
- `AUTH_MODE=local_single_user` 仅记录为 G5 企业 IdP deferred，不阻断当前范围；
- 原始完整格式范围保持 6x10/60，audio 为 `deferred_by_user` 且 `counted_in_current_scope=false`；
- 当前范围严格为 pdf_text、pdf_scanned_or_image、docx、pptx、spreadsheet 五类 5x10/50；真实样本 0/50，Gate 保持 BLOCKED；
- ASR 三键保持空白且当前 nonblocking，历史 contract/Stub/测试/未来恢复入口未删除；
- 文档中旧的 APP Secret 长度不足结论已删除；
- 全量质量门：pytest 201 passed、Ruff 191 files、mypy 87 source files、OpenAPI 27 paths、SQLite v11、G4 本地安全/性能/恢复 Harness、Vue、密钥扫描和 Docker 禁止项全部通过；
- `real_acceptance=false`、`real_external_call_performed=false`，Git HEAD 未变化，保持 NO_COMMITS。

## 当前阻断

- 五类各 10 份、共 50 份真实样本及 metadata；
- 真实模型预算与调用授权；
- MySQL G3/G4 migration 授权；
- external lifecycle 对账、撤权、删除、清理和恢复 drill 授权。

企业 IdP 与 ASR/audio 按用户决定暂缓，不重复请求。不得进入 G5。
