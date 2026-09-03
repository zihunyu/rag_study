# Future error-case retest runners r2 独立审核

审核结论：`CHANGES_REQUIRED:UAT_FUTURE_ERROR_RETEST_INDEPENDENT_RENDER_PROOF`

已确认 r2 已把 source classification 传入 future case，并在 HTTP transport 创建前接入出站
策略校验；动态范围、独立 v2 namespace、预算与 retry 边界均正确。

但 render proof 不是独立证明：fresh-source preparation 将 `rendered_text` 直接赋为同一
`content` 变量。该恒等赋值只能证明字符串等于自身，不能检测图片、PDF 文本层、字体或渲染
表示中的缺字/错字/不一致，因而不能关闭 T5，也不应把 14 条标为具有 verified render proof。

r3 必须使用与解析文本独立的、本地可复现 render/representation 来源，并记录其来源 revision、
hash 与 locator 对齐证明；若某格式或 locator 没有这种独立证明，必须动态写入 content-free
`BLOCKED` 且 provider=0。禁止以 `rendered_text=content`、任意同值复制或任何特定问题/答案
例外替代该证明。重新冻结输入计划和 eligible/blocked 统计后再提交审核；继续禁止外部调用。
