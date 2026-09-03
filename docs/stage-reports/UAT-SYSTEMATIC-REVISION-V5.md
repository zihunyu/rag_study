# UAT 39 条 two-term 系统性修订 v5

状态：`PENDING_USER_REVIEW`

- 范围：positions 40–78，共 39 条；既有 PASS 39 条保持只读；
- 算法：`uat-systematic-two-positive-terms:v5`；每条选择两个 positive-only terms；
- 两个 term 均存在 positive 且不在任一 distractor；优先不同 token family、非包含重叠、
  更短 pair，并使用稳定 hash 决胜；
- 39/39 使用两个唯一 term，39/39 非包含重叠，28/39 不同 token family；
- 新问题仅由固定模板、当前问题与两个 terms 构成，不加入证据外事实；
- evidence documents、IDs、roles、locators、content 与 content hashes 完全不变；
- review ref：`uat-systematic-revision-v5/approved-review.json`，SHA-256
  `6ecd5ef50fae97805aa35496dfbf795dfe6038e01ac876bf1cb10714954e68b2`；
- manifest ref：`uat-systematic-revision-v5/manifest.json`，SHA-256
  `c6af47f4c19b704d57d80ad5c17dc95cd23f4c6a58080cb8eff14975266eeb80`；
- category：DOCX 1、PPTX 25、Spreadsheet 13；真实问题文本仅在本地 artifact；
- v5 plan：39 existing PASS + 39 revised；checkpoint `uat-reranker-v5.json`、max 39、
  top-k 2、retry 0；当前 approved=false/executed=false/checkpoint 不存在；
- LLM 对 v5 revised set 未获批准；本阶段模型/网络调用 0。

STAGE_REVIEW_REQUESTED:UAT_SYSTEMATIC_REVISION_V5_READY
