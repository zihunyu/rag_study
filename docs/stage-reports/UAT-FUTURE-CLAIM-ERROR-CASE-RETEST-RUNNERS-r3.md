# Future structured-claim error-case retest runners r3 fail-closed report

审查状态：`STAGE_REVIEW_REQUESTED:UAT_FUTURE_CLAIM_ERROR_CASE_RETEST_RUNNERS_READY:r3`

## 独立 render proof

r3 不再把 parser 文本复制为 render proof。proof 来源为独立的本地 raw-source representation：PDF 使用页文本表示、PPTX 使用 slide shape 表示、表格使用原始单元格/CSV 表示；每条 proof 绑定 `uat-independent-render-proof:v1`、source version SHA、locator SHA 和 representation SHA。缺少该表示或与 fresh parser evidence 不一致时 fail-closed。

## 动态 preflight 结果

- v3 plan SHA-256：`a0477ce29e0e2b3fa578dd977e615ea3dcb0bec61956efd7f497af7dedaa8730`；input manifest SHA-256：`54bd81b8c92b2fe6e83042094d72178b9d4bd18e42a0e470c6dac2f28cecdd08`。
- 动态 selected=15，eligible=0，BLOCKED=15；全部 BLOCKED record 的 provider call count 为 0。
- BLOCKED 类型汇总：独立 render proof 不可用 9；source/render representation mismatch 5；source 控制字符 1。
- 因 eligible=0，future LLM 不具备可执行输入；max=15 仅为已批准的上限，不消耗预算。

## 其他边界

- future case 的 source classification 仍会在真实 execute 前经 existing provider egress policy 校验；plan 保持 `approved_by_user=false`、`executed=false`。
- anti-content scan（14 文件）仍为 0 历史 literal matches / 0 case-ID literal；secret scan finding=0。
- provider=0、Zilliz=0、Docker=0、模型重跑=0、commit=0；历史 v1–v5 artifacts 未改写。

r3 仅提交安全阻断证据，不能请求 future provider 执行。需要先提供或生成能够通过独立 render representation 校验的 fresh source inputs，并经用户重新批准后，才可形成 eligible retest 集合。
