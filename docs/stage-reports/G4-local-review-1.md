# G4 本地验证准备第一次审核

审核结果：`CHANGES_REQUIRED:G4_LOCAL_PREPARATION`

审核时间：2026-09-01T12:55:30+08:00

提交策略：`NO_COMMITS`。

## 已通过

- 外部输入清单仅列键名、样本类别和需授权动作，没有输出配置值或伪造样本；
- 六类真实难例保持 0/60，格式 Gate 明确 BLOCKED，`real_acceptance=false`；
- OCR、旧 Office 和 ASR 使用离线 Stub 并携带 blocked quality issue；
- 单文档 quality/review API、SQLite v11、OpenAPI、Vue 基础交互和文档骨架已建立；
- 全量质量门通过：180 tests、Ruff 184 files、mypy 84 source files、OpenAPI 27 paths、SQLite v11、前端测试/build、密钥扫描和 Docker 禁止项；
- 没有真实外部调用、计费请求、云端修改或项目真实数据恢复。

## 必须修复

### 1. P0：合法二进制图片/音频会误走 UTF-8 文本校验

`UploadFileValidator.inspect()` 使用“无效 magic 的 elif”链，合法 WAV 等分支条件为 false 后最终落入文本 `read_text()`。独立探针用 Python `wave` 生成的真实合法 WAV，通过 create/upload 后在 complete 返回 422 `DOC_TEXT_ENCODING`。

当前测试使用仅含 ASCII 的伪 RIFF/MP3/M4A 字节，因此掩盖了真实二进制失败。

修复标准：按扩展名显式分支；二进制格式验证 magic 后直接构造 detected result，只有 text/markdown/html/csv 执行 UTF-8 校验。增加真实生成的 WAV、有效 PNG/JPEG/GIF/TIFF、MP3 frame/ID3 与 M4A box 上传 API 回归，并覆盖截断/伪造 magic 负向用例。

### 2. P0：`BLOCKED_REAL_VALIDATION` Stub 可以无 review 发布为 SERVING

独立 API E2E 使用合法 OLE header 的 `.doc`：Worker 产出 quality disposition=`BLOCKED_REAL_VALIDATION`、`real_acceptance=false`，数据库无 review 记录；publish 仍返回 200，version 变为 SERVING。

这允许 OCR/ASR/旧 Office Stub 绕过真实格式 Gate 对 reader 发布，也说明新建的人工 review 尚未接入 PublicationReadiness。

修复标准：PublicationReadiness 同时要求最新 quality report、允许的 disposition、明确 APPROVED review 和 review/quality revision；`BLOCKED_REAL_VALIDATION` 永远不能被人工 APPROVED 覆盖为真实可发布。未 review、NEEDS_REWORK、REJECTED、旧 revision、Stub blocked 均 409 且零副作用。真实样本未到位前 Stub 文档只能管理预览，不得 serving。

### 3. P1：Prompt injection Harness 只统计清单，没有执行安全行为

`build_g4_local_validation_report()` 只读取 YAML、统计 8 条并检查 `synthetic=true`，没有把 payload 送入 QA/检索/引用/egress/RBAC 路径，也没有验证各 `expected`。

修复标准：为 8 个 fixture 建立确定性 runner，逐条执行对应本地安全链路，记录 case ID、实际结果、expected、passed；必须验证 0 hidden retrieval、0 cross-tenant text、0 forged citation、0 external egress。报告只在 8/8 时 local security ready。

### 4. P1：性能/容量/长稳 Harness 只是 SHA-256 循环和静态数组

当前只执行 2,000 次 checksum，并把 documents/concurrency/Top-K/answer length 数组写入报告；没有按这些组合运行上传/队列/检索/问答/发布路径，`long_run_iterations=100` 也只是常量。

修复标准：用小型合成语料实际运行代表性本地工作流，至少覆盖若干 data scale、concurrency 和 Top-K 组合；长稳迭代必须真实循环并记录成功/失败/延迟/资源上下文。可以保持规模很小且 `slo_claimed=false`，但不能用无关 checksum 冒充系统性能。

### 5. P1：backup/restore 使用玩具两表库，不是项目 Schema v11

当前恢复探针手工创建 `documents`/`tombstones` 两张简表，再手工把 visible 改为 0；没有恢复真实 SQLiteDatabase、队列、lifecycle、reference、rag、publication、lineage 和文件内容，也没有通过应用读取路径验证 fail-closed。

修复标准：在 TemporaryDirectory 中创建真实 Schema v11 和生成的本地文件/队列/生命周期数据，使用 SQLite backup API 和文件副本恢复到新目录，重建 RuntimeComponents；验证 tombstone、引用撤销、publication current pointer、cleanup outbox、queue 幂等和本地文件哈希。必须继续 `real_project_data_touched=false`。

### 6. P2：空 CanonicalDocument 的质量报告会除零

`DocumentQualityReport.from_document()` 在 `node_count=0` 时计算 `located / node_count`。应返回 coverage=0、明确 EMPTY_DOCUMENT issue 和 DEGRADED/BLOCKED，而不是抛异常。

## 外部边界

上述均为本地合成修复，不需要 ASR、真实样本或外部授权。继续保持真实 G4 blocker、NO_COMMITS、禁止外部调用和禁止进入 G5。
