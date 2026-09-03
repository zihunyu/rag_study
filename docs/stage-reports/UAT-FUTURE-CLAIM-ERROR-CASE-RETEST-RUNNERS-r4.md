# Future error-case retest r4 readiness

审查状态：`STAGE_REVIEW_REQUESTED:UAT_FUTURE_CLAIM_ERROR_CASE_RETEST_RUNNERS_READY:r4`

r4 修正 render proof propagation：raw preflight proof 现严格传入 evidence envelope、future case、claim contract 与 content-free audit record，并绑定 proof revision、source-version SHA、locator SHA、representation SHA。定向 preflight 测试通过，证明具备有效 proof 的通用 case 不会被错误 BLOCKED，proof 缺失/变异仍 fail-closed。

为保持不可变性，修订后输入使用独立 `uat-future-error-retest-v4` root 和 `uat-future-error-retest-v4-plan.json`，未覆盖 v1–v3 或任何历史 UAT artifact。

动态 v4 preflight 当前仍为 selected=15、eligible=0、BLOCKED=15；这是独立 representation 缺失/不一致与 source-integrity Gate 的安全结果，所有 BLOCKED provider=0，future plan 未执行。定向 tests=20 passed；anti-content scan=0；secret scan=0；provider/Zilliz/Docker/model rerun/commit=0。
