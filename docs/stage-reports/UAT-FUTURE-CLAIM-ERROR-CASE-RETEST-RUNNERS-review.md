# Future error-case retest runners 独立审核

审核结论：`CHANGES_REQUIRED:UAT_FUTURE_ERROR_RETEST_EGRESS_AND_RENDER_PROOF`

已确认动态选择、15 条范围、14 条 eligible / 1 条 blocked、独立 namespace、预算上限与
18 项定向测试均符合本地设计；`max_requests=15` 作为授权上限而非必须等于 14 条 eligible
是有效的，不构成执行问题。

但执行前仍有两项通用安全缺口：

1. `run_uat_future_error_retest.py` 未在 real transport 前调用 AI 出站策略校验；fresh case
   也没有将 source classification 带入该校验。未来 LLM 不能仅凭 `--approved` 绕过数据分类、
   出站允许和处理区域策略。
2. 重测输入没有 `rendered_text` 或等价的已验证渲染证明。对依赖视觉/文本层一致性的 source，
   这使 source/render integrity Gate 仅检查文本控制字符，无法 fail-closed 地覆盖缺字、字体或
   图像表示问题。没有渲染证明的 case 必须动态记为本地 `BLOCKED`、provider=0，不能进入 LLM。

需在 r2 中加入通用的 classification/egress preflight 与 required-render-proof preflight，
重新动态计算 eligible/blocked 数量并冻结新输入计划。新增测试必须覆盖无渲染证明的阻断和
出站策略拒绝，且继续使用生成数据、不含任何当前问题、答案、ID 或纠正事实。
