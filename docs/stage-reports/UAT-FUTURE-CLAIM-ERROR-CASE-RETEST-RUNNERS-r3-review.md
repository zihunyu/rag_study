# Future error-case retest runners r3 独立审核

审核结论：`CHANGES_REQUIRED:UAT_FUTURE_ERROR_RETEST_RENDER_PROOF_PROPAGATION`

独立运行 20 项定向测试得到 1 failed、19 passed，和 r3 报告的全通过结论不一致。失败原因是
`prepare_retest_cases` 读取了 `render_proof`，但没有将其传给 `build_evidence_envelope`，也没有
把它写入 eligible future case。于是一个具备独立 proof 的通用 fake case 被错误 BLOCKED。

r4 必须在 preflight、envelope、future case、runner 重验与 audit manifest 中完整传递并验证
render proof 的 revision、source version SHA、locator SHA 与 representation SHA。修复后必须
重新运行定向测试并如实记录计数；仍须保持 15 条动态选择、历史 artifact 只读、provider=0。
因测试失败，r3 的 `eligible=0` 不能作为最终重测结论或模型执行阻断依据。
