# UAT 75 条系统性修订 v4

状态：`USER_APPROVED_RUNNER_REVIEW_REQUIRED`

- 范围：原 candidates 4–78，共 75 条；已有 PASS 的 3 条保持只读；
- 算法：`uat-systematic-positive-terms:v4`，确定性 CJK/ASCII 关键词、短语及有序行边界，
  只接受 positive 中存在且所有 distractors 中不存在的 term；
- 每条推荐问题由固定模板、原 question 与一个 positive-only term 构成，不增加证据外事实；
- term 长度 4–20；75/75 均成功生成，否则整套不会落盘；
- evidence documents、evidence IDs、roles、locators、content 与 content hashes 逐项不变；
- review ref：`uat-systematic-revision-v4/approved-review.json`，SHA-256
  `b3be4dd16601548ee27dc9551461f5fe87759f3721383595cb5abdc16e42d670`；
- manifest ref：`uat-systematic-revision-v4/manifest.json`，SHA-256
  `30b996d1f0f7ab9b5e5dd2b0bb6ce23c2845a1cdca5f4434a5fa1f064f4b56af`；
- category：DOCX 20、扫描/图片 10、文本 PDF 7、PPTX 25、Spreadsheet 13；
- 真实原问题与修订问题只在本地 artifact，报告/终端没有正文；
- 用户已批准全部 75 条修订、Reranker v4 max 75/retry 0，并条件批准 combined 78/78 后
  LLM v3 max 78/retry 0；当前仅等待 runner 审核；
- v4 checkpoint、combined-v4、LLM-v3 checkpoint 与 results-v3 均不存在；本阶段网络 0。
- 完整质量门：297 tests passed、Ruff 308 files、mypy 108 source files、frontend、OpenAPI、
  config、全部旧/新 UAT artifacts 与 secret scan 通过；39 checks、failed 0、skipped 0。

STAGE_REVIEW_REQUESTED:UAT_SYSTEMATIC_REVISION_V4_READY
