# UAT systematic v5 通用修复集成就绪审查 r2

审查状态：`STAGE_REVIEW_REQUESTED:UAT_SYSTEMATIC_V5_GENERIC_REMEDIATION_READY`（修订 `r2`）

## 对上一轮审查意见的修正

上一版只提供库级规则。本修订将其接入全新的 future-only 执行路径，历史 v1–v5 runner、结果、checkpoint 和 Gate 不被引用为输入或写入目标。

- `FutureUatClaimRunner` 仅依赖 contracts ports，并以独立 namespace、future checkpoint、`uat-claim-results/v1` 和 `uat-claim-audits/v1` 运行。
- `UatClaimContractHttpTransport` 只接受 `uat-claim-contract:v1`，要求模型返回结构化 status/claims；不允许 free-form answer。
- `plan_uat_future_claim_remediation.py` 固定 future-only plan：`approved_by_user=false`、`executed=false`、provider/network/model calls=0，计划 SHA-256 为 `c089417c0147203be65cfda35daffc6ddcf7e12287963606c4393420580d85f7`。

## 已接线的通用闭环

1. 原始 future evidence 入池时强制 source integrity，保存并重新验证 source/render verified 标志、各自 hash 与 integrity snapshot hash。
2. 每条 evidence 生成携带 source document/version、content/span、locator、可选 entity ID/field key 的 envelope；两个 optional 字段必须与 claim 同时一致，允许同为 null。
3. LLM future request 使用 claim contract，带完整 provenance；runner 只在结构化 response 通过 claim/provenance/status validator 后继续。
4. locator grounded 由已验证 claim 的 locator hash 推导；非 answered 状态不能携带 claim。
5. 结果与 content-free audit manifest 先以独立 future 根目录原子持久化，再将 ref/SHA 写入 future checkpoint；coverage validator 强制 case 1:1。
6. source corruption、render mismatch、claim value/provenance 变异均在 transport 调用前或首条失败时停止，automatic retry=0；future runner 不触碰历史执行路径。

## Fake 全链路与无内容定向证据

- fake transport 覆盖两个未来 case 的 source envelope → claim contract → response validator → future result/audit persistence → checkpoint resume；第二次运行无重复调用。
- rendered/source 不一致在 transport 前拒绝；无效 claim 在首条后停止，后续 case 不发送。
- 新增/修改的 10 份 remediation 实现、adapter、contracts、plan、scan 和测试文件由专门扫描检查。动态比较审核包中的 78 个历史 ID、78 个答案、17 个纠正引用，历史 literal matches=0，20-hex case-ID literals=0。
- 不包含当前问题、答案、姓名、日期、实体或事实的规则、替换表、提示词例外或样本修改。

## 质量与隔离

- fake/性质/变异与架构边界定向测试 16 passed。
- 全量 pytest 收集 318 条；Ruff 341 files；mypy 111 source files；完整质量门 42 项 `failed=0`、`skipped=0`；frontend、OpenAPI、config 和 secret scan 均通过，secret finding 0。
- 历史 SHA 未变：Reranker v5 `188aded52174b60e399ea9d6448ffe383d5c553a050418581bfd20388e256317`；LLM v4 `7163f71791a974afac036e6eb8a6106f0de870275da47564b4d44359753cbb62`；combined-v5 Gate `ad5920fd2050797d4aaf3c952b68bffc515e9cc9adadb3a3dfd7f6eaf1f48d6b`。

本修订没有 external provider、Zilliz、Docker、模型重跑或提交。future-only path 尚未执行，必须在独立审核与后续用户授权后才可提交新的完整 UAT 输入。
