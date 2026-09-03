# G4 外部输入清单

状态：`BLOCKED_EXTERNAL_INPUTS`。本清单只列名称，不包含任何配置值或真实数据。

## 当前配置状态

- `APP_SECRET_KEY` 已配置并通过长度检查；只报告 configured/valid 状态，不输出值；
- `ASR_ENABLED=false`，ASR 三键按用户决定暂缓且不阻断当前范围；
- 企业 IdP 继续 deferred，不重复请求。

## 真实样本类别

- 文本 PDF：10 份难例；
- 扫描 PDF / 图片：10 份难例；
- DOCX：10 份难例；
- PPTX：10 份难例；
- XLS/XLSX/CSV：10 份难例；
- WAV/MP3/M4A：原始完整范围 10 份，当前 `deferred_by_user`，不计入 ready。

当前需提供前五类各 10 份、共 50 份。每份样本必须提供 `sha256`、脱敏确认、授权引用、权利确认、分类级别和预期参考定位。
不得伪造真实样本，也不得复制未批准业务数据。

## 需另行授权的动作

- 真实 LLM、MinerU、Embedding、Reranker 计费请求；
- MinerU 真实文档处理；
- MySQL G3/G4 DDL 或数据迁移；
- Zilliz/Redis/MySQL 对账写入、清理、切换、恢复；
- 云端撤权、删除、备份恢复；
- 使用真实项目数据做容量、长稳或恢复演练。

当前 remaining blockers 仅为五类 50 份样本、真实模型授权/预算、MySQL G3/G4 migration、
外部 lifecycle 对账/撤权/删除/恢复演练。ASR/audio 与企业 IdP 均 deferred。
