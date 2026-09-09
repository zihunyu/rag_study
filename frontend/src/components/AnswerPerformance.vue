<script setup>
import { computed } from 'vue';
const props = defineProps({ report: Object });
const labels = { 'rag.ask.evidence.build': '检索与资料准备', 'rag.ask.llm.generate': '生成回答', 'rag.ask.claim.verify': '答案与条件核验', 'rag.ask.claim.reverify': '补全后重新核验', 'rag.ask.conditions.repair': '补全遗漏条件', 'rag.ask.permission.final': '最终权限检查', 'rag.ask.citation.verify': '生成引用', 'verification.conditions': '条件批次' };
const stages = computed(() => (props.report?.events || []).filter(e => e.kind === 'stage' && labels[e.name]));
const calls = computed(() => (props.report?.events || []).filter(e => e.kind === 'model_http'));
const queue = computed(() => calls.value.reduce((sum, e) => sum + (e.queue_seconds || 0), 0));
const cacheHit = computed(() => props.report?.events?.some(e => e.kind === 'cache' && e.cache === 'verified_result' && e.outcome === 'hit'));
const seconds = n => Number(n || 0).toFixed(2);
labels['verification.conditions.retry'] = '超时批次缩小重试';
labels['conversation.resolve'] = '会话问题解析';
labels['rag.ask.citations.repair'] = '补齐引用';
labels['rag.ask.citations.reverify'] = '引用补齐后完整核验';
labels['rag.ask.answer_surface.rebuild'] = '按已核对事实重组回答';
labels['rag.ask.answer_surface.reverify'] = '重组回答后完整核验';
labels['rag.ask.source_list.compose'] = '按原文编号整理条目';
</script>
<template>
  <details v-if="report" class="technical answer-performance">
    <summary>处理耗时 {{ seconds(report.elapsed_seconds) }} 秒<span v-if="cacheHit"> · 已复用验证结果</span></summary>
    <p v-if="cacheHit">问题、资料版本、配置与权限一致；返回前已重新检查当前权限与发布状态。</p>
    <p>模型请求 {{ calls.filter(c => c.sent).length }} 次 · 累计额度排队 {{ seconds(queue) }} 秒</p>
    <p v-for="(stage, index) in stages" :key="index">{{ labels[stage.name] }}<template v-if="stage.batch_number"> {{ stage.batch_number }}</template>：{{ seconds(stage.seconds) }} 秒<span v-if="stage.status === 'failed' || stage.status === 'error'">（未完成）</span></p>
    <p v-if="stages.some(s => s.batch_number)" class="muted">条件批次可能并行，阶段耗时不能直接相加。</p>
  </details>
</template>
