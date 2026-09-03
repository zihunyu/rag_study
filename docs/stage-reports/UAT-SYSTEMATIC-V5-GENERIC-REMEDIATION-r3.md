# UAT systematic v5 通用修复边界就绪审查 r3

审查状态：`STAGE_REVIEW_REQUESTED:UAT_SYSTEMATIC_V5_GENERIC_REMEDIATION_READY`（修订 `r3`）

## r3 关闭的边界

- **跨文档策略一致性**：`allow_cross_document` 从 future case binding 传入 claim contract、claim validator 和 audit manifest。允许时，两个不同 source document 的结构化 claims 可完成 result/audit/coverage 持久化；默认禁止时，相同形态在首条 future request 后拒绝，后续 case 不发送。
- **不可变 coverage**：future runner 在首次完成和所有 resume 中都从 content-free audit manifests 重建 1:1 coverage，绑定输入快照和每个 audit ref/SHA，并原子持久化 `coverage.json`。任何已完成集合的缺失 audit、缺失 coverage 或 coverage 内容错配都会在不发送新 request 的情况下拒绝；只在可重建且一致时才接受 completed 集合。
- **计数可复现**：两个新增 future runner 测试文件与通用性质/变异文件共 16 项定向测试，计数与本报告一致。

## Future-only 闭环与隔离

新 runner 继续只通过 contracts ports 使用 versioned structured-claim transport；raw evidence 必经 source/render integrity、envelope、claim/provenance/status validator 和 derived locator，再写入 future result/audit/coverage 根目录。历史 v1–v5 results、checkpoints 与 Gate 均未被运行时路径读取或写入。

future plan `c089417c0147203be65cfda35daffc6ddcf7e12287963606c4393420580d85f7` 保持 `approved_by_user=false`、`executed=false`、provider/network/model calls=0。coverage 在 fake tests 的临时目录验证；未在项目 artifact 根目录生成 future execution output。

## 非内容定向与质量证据

- 抗内容扫描覆盖 10 个本次新增/修改的 remediation 实现、adapter、contracts、plan、scan 和测试文件。动态比较审核包中的 78 个历史 ID、78 个答案、17 个纠正引用，历史 literal matches=0，20-hex case-ID literals=0。
- 定向 tests=16 passed；全量 pytest=320 collected；Ruff=342 files；mypy=111 source files；完整质量门=42 checks，`failed=0`、`skipped=0`；secret scan finding=0。
- 历史 SHA 未变：Reranker v5 `188aded52174b60e399ea9d6448ffe383d5c553a050418581bfd20388e256317`；LLM v4 `7163f71791a974afac036e6eb8a6106f0de870275da47564b4d44359753cbb62`；combined-v5 Gate `ad5920fd2050797d4aaf3c952b68bffc515e9cc9adadb3a3dfd7f6eaf1f48d6b`。

provider=0、Zilliz=0、Docker=0、模型重跑=0、commit=0。future path 尚未获执行授权；需要独立审核后才能讨论新的 UAT 输入或调用预算。
