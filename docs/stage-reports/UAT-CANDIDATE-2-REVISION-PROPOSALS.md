# UAT Candidate 2 本地改写建议

状态：`PENDING_USER_REVIEW`

- 来源复核 artifact hash 已验证；documents 4，role 为 positive 1 + distractor 3；
- 使用 `uat-candidate-revision-terms:v1` 确定性本地算法；支持 CJK n-gram 与 ASCII token，
  排除过短项、纯数字噪声、敏感键及其赋值；
- proposals 3；每条只复用原 question 与 positive 中的区分性词，不增加证据外事实；
- artifact ref：`uat-result-review/candidate2-revision-proposals.json`；
- artifact SHA-256：`f281ace99a60efa8ba64c0ead0002e1f9f052993da1126e4b9d3e9c19a2952e7`；
- 真实原问题与 proposal 文本仅存在本地 artifact，未写入终端或报告；
- approved/pending、v1 checkpoint、78 bundles 共 81 个输入 hash 均保持不变；
- Reranker/LLM/Embedding/MinerU/Zilliz/网络调用 0；v2 checkpoint 不存在；
- 未经用户审核，不修改 candidate、approved/pending 或 bundle，不执行 v2。
- 完整质量门：288 tests passed、Ruff 288 files、mypy 106 source files、frontend、OpenAPI、
  config、failure-review、proposal 复验及 secret scan 全部通过；failed 0、skipped 0。

STAGE_REVIEW_REQUESTED:UAT_CANDIDATE_2_REVISION_PROPOSALS_READY
