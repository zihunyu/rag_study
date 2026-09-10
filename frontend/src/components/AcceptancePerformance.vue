<script setup>
defineProps({ summary: Object });
const seconds = n => Number(n || 0).toFixed(2);
const stages = { 'rag.ask': '问答', 'rag.ask.evidence.build': '资料准备', 'rag.retrieval': '检索', 'rag.retrieval.embedding': '问题向量化', 'rag.retrieval.rerank': '重排', 'rag.ask.llm.generate': '首次生成', 'rag.ask.claim.verify': '首次核验', 'rag.ask.claim.reverify': '补充后再次核验', 'rag.ask.citations.reverify': '引用修复后核验', 'rag.ask.answer_surface.reverify': '格式修复后核验', 'rag.ask.citations.repair': '引用修复', 'rag.ask.conditions.repair': '补全条件', 'evidence.visual_query': '逐图识别与核验', 'verification.conditions': '条件批次', 'verification.conditions.retry': '条件批次重试', 'verification.conditions.protocol_repair': '仅重查回执异常的条件' };
const stageLabel = path => path ? path.split(' / ').map(s => stages[s] || s).join(' / ') : '阶段未记录';
</script>
<template><details v-if="summary?.stages" class="acceptance-performance"><summary>问答耗时与调用明细 · {{ seconds(summary.elapsed_seconds) }} 秒</summary>
  <p class="small muted">仅统计本次问答，独立验收评审另计。资料准备包含检索、筛选及图片复核；下表各大阶段不重复累计子阶段。</p>
  <table><thead><tr><th>阶段</th><th>耗时</th></tr></thead><tbody><tr v-for="s in summary.stages" :key="s.key"><td>{{ s.label }}</td><td>{{ seconds(s.seconds) }} 秒</td></tr><tr><td>其他与未细分时间</td><td>{{ seconds(summary.other_seconds) }} 秒</td></tr></tbody></table>
  <p>修复后再次核验 {{ summary.reverification_count }} 轮 · 未成功调用 {{ summary.failed_calls }} 次 · 已识别相同请求再次发送 {{ summary.same_request_again_count }} 次</p>
  <p class="small muted">相同请求再次发送可能是超时重试，不自动判为无效调用。回答或引用改变后的再次核验仍有必要。旧记录未标注调用阶段和请求身份时显示“未记录”。并行调用耗时不能相加作为用户等待时间。</p>
  <p v-for="(c, i) in summary.cache" :key="i">缓存 {{ c.cache }}：{{ c.outcome === 'bypass_acceptance' ? '验收主动绕过答案缓存，测量实际问答' : c.outcome }}</p>
  <div class="acceptance-table"><table><thead><tr><th>调用</th><th>阶段 / 模型</th><th>排队</th><th>请求耗时</th><th>结果</th><th>重复识别</th></tr></thead><tbody><tr v-for="c in summary.calls" :key="c.number"><td>{{ c.number }}</td><td>{{ stageLabel(c.stage) }}<br>{{ c.model }} · {{ c.role }}</td><td>{{ seconds(c.queue_seconds) }} 秒</td><td>{{ seconds(c.network_seconds) }} 秒</td><td>{{ c.outcome }}{{ c.sent ? '' : '（未发送）' }}</td><td>{{ !c.identity_recorded ? '未记录' : c.same_request_again ? '相同请求再次发送' : '本请求首次发送' }}</td></tr></tbody></table></div>
</details></template>
