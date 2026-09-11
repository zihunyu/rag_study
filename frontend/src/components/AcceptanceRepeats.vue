<script setup>
defineProps({ summary: Object, comparison: Object });
const seconds = n => n == null ? '未记录' : `${Number(n).toFixed(2)} 秒`;
const groups = { answered: '已回答', insufficient_evidence: '资料不足', conflicting_evidence: '证据冲突', running: '运行中', system_failure: '系统失败或中断', other: '其他结果', not_run: '未运行' };
const assessments = { not_comparable: '标准或资料不同，不能直接比较', repeat_count_changed: '重复次数不同', incomplete: '尚未完成或耗时记录不全', outcome_changed: '回答状态改变，不能计为提速', quality_failed: '质量检查未通过', human_review_pending: '待人工复核，尚不能认定无损提速', quality_verified: '质量已复核，可比较耗时' };
</script>
<template>
  <section v-if="summary?.rows?.length" class="panel acceptance-table">
    <h2>重复运行统计</h2>
    <p>共计划 {{ summary.planned_samples }} 次问答。中位数和最慢值包含恢复前的问答耗时，暂停等待不计入；重试不算新的独立样本。每次调用费用仍计入整轮用量。</p>
    <p v-for="g in summary.groups" :key="g.group">{{ groups[g.group] }}：{{ g.count }} 次 · 中位数 {{ seconds(g.median_seconds) }} · 最慢 {{ seconds(g.worst_seconds) }}</p>
    <table><thead><tr><th>案例</th><th>已完成</th><th>程序检查通过</th><th>模型建议通过</th><th>人工确认通过</th><th>中位数 / 最慢</th></tr></thead><tbody>
      <tr v-for="r in summary.rows" :key="r.case_id"><td>{{ r.key }}</td><td>{{ r.completed }}/{{ r.planned }}</td><td>{{ r.mechanical_passed }}/{{ r.planned }}</td><td>{{ r.assisted_passed }}/{{ r.planned }}</td><td>{{ r.human_passed }}/{{ r.planned }}</td><td>{{ seconds(r.median_seconds) }} / {{ seconds(r.worst_seconds) }}</td></tr>
    </tbody></table>
    <p class="small muted">“模型建议通过”不能替代人工确认。不同回答状态分组展示；资料不足和系统失败的短耗时不能作为正常回答提速。</p>
  </section>
  <section v-if="comparison?.rows" class="acceptance-table">
    <h3>重复运行对照</h3>
    <p v-if="comparison.snapshot_differences.length">本次变化的快照字段：{{ comparison.snapshot_differences.join('、') }}。代码或配置差异是本次实验变量，资料、案例与权限差异需另行核对。</p>
    <table><thead><tr><th>案例</th><th>中位数</th><th>最慢</th><th>中位数变化</th><th>结论</th></tr></thead><tbody><tr v-for="r in comparison.rows" :key="r.case_id"><td>{{ r.key }}</td><td>{{ seconds(r.before?.median_seconds) }} → {{ seconds(r.after.median_seconds) }}</td><td>{{ seconds(r.before?.worst_seconds) }} → {{ seconds(r.after.worst_seconds) }}</td><td>{{ r.median_change_percent == null ? '不可比' : `${r.median_change_percent}%` }}</td><td>{{ assessments[r.assessment] }}</td></tr></tbody></table>
  </section>
</template>
