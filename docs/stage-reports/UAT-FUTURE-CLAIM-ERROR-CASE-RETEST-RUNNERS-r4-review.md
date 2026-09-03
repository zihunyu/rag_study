# Future error-case retest runners r4 独立审核

审核结论：`CHANGES_REQUIRED:UAT_FUTURE_ERROR_RETEST_V4_NAMESPACE_ISOLATION`

r4 的 render proof propagation 已通过 20 项定向测试，且 v4 preflight 为 selected=15、eligible=0、BLOCKED=15、provider=0。但 v4 执行计划未完整隔离：

- `run_uat_future_error_retest.py plan` 仍读取并输出 v3 plan；
- v4 plan 的 input manifest 指向 v4，但 blocked ref、runner revision、checkpoint、result、audit 与 coverage refs 仍指向 v3。

这会让未来执行路径混用 v3/v4 inputs，违反不可变隔离，不能放行。r5 必须使 script、preflight blocked ref、runner revision 和全部 checkpoint/result/audit/coverage refs 一致指向 v4；plan 模式必须显示 v4 的零调用状态。修正后重新运行定向测试与隔离检查，且继续禁止 provider 调用。
