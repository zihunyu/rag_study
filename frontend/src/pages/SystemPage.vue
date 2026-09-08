<script setup>
import { computed, onMounted, ref } from 'vue';
import { Activity, Database, HardDrive, Network, Cpu, RotateCw, Check, CircleAlert, CircleHelp, Server } from '@lucide/vue';
import { request } from '../api.js';
import { useResource } from '../composables/useResource.js';
import { dateTime, fileSize } from '../format.js';
import ErrorNotice from '../components/ErrorNotice.vue';
const resource = useResource(signal => request('/system/status', { signal }));
onMounted(resource.load);
const data = resource.data;
const state = value => ['ready','ok'].includes(typeof value === 'string' ? value : value?.state) ? 'ready' : ['unavailable','insufficient_space','circuit_open','stalled'].includes(typeof value === 'string' ? value : value?.state) ? 'failed' : 'unknown';
const items = computed(() => {
  const deps = data.value?.dependencies || {};
  const base = [{ key: 'mysql', name: 'MySQL', detail: '文档、版本与对话持久化', icon: Database }, { key: 'sqlite', name: '本地数据库', detail: '本地运行时持久化存储', icon: Database }, { key: 'queue', name: 'Redis 处理队列', detail: '文件任务与重试状态', icon: Network }, { key: 'retrieval_release', name: '检索控制面', detail: '索引代次与来源有效性', icon: Server }, { key: 'storage', name: '文件存储', detail: deps.storage ? `可用空间 ${fileSize(deps.storage.free_bytes)}` : '原文件与解析产物', icon: HardDrive }].filter(item => deps[item.key] != null).map(item => ({ ...item, status: state(deps[item.key]) }));
  for (const [key, label] of Object.entries({ embedding: '向量模型', reranker: '重排模型', generator: '回答模型', verifier: '答案验证模型' })) {
    const provider = deps.providers?.[key];
    if (provider) base.push({ key, name: label, icon: Cpu, status: provider.circuit_open || provider.consecutive_failures ? 'failed' : provider.last_success_epoch ? 'ready' : 'unknown', detail: provider.last_success_epoch ? `最近成功 ${dateTime(provider.last_success_epoch)}` : '尚无本次运行的成功调用记录' });
  }
  if (data.value) base.push({ key: 'worker', name: '文件处理 Worker', detail: data.value.worker.reason, icon: Cpu, status: state(data.value.worker) }, { key: 'parser', name: 'MinerU 文档解析', detail: data.value.parser.state === 'configured' ? '已配置；解析调用尚未在此页检测' : '尚未配置远程解析服务', icon: FileIcon, status: 'unknown' });
  return base;
});
const FileIcon = HardDrive;
const reasons = { MYSQL_UNAVAILABLE: '文档数据库连接失败', REDIS_QUEUE_UNAVAILABLE: '处理队列连接失败', RETRIEVAL_RELEASE_UNAVAILABLE: '检索控制状态读取失败', LOCAL_STORAGE_FREE_SPACE_LOW: '本地文件存储空间不足', EMBEDDING_CIRCUIT_OPEN: '向量模型连续调用失败', RERANKER_CIRCUIT_OPEN: '重排模型连续调用失败', GENERATOR_CIRCUIT_OPEN: '回答模型连续调用失败', VERIFIER_CIRCUIT_OPEN: '答案验证模型连续调用失败', WORKER_HEARTBEAT_UNAVAILABLE: '文件处理进程已停止或心跳过期', WORKER_TASK_STALLED: '文件处理任务长时间没有进展' };
const labels = { ready: '正常', failed: '异常', unknown: '未检测' };
</script>
<template><div class="page"><div class="page-heading"><div><p class="eyebrow">SYSTEM HEALTH</p><h1>运行状态，一目了然。</h1><p class="page-description">根据实际探针与调用记录，了解知识库的运行情况。</p></div><button class="btn primary" :disabled="resource.loading.value" @click="resource.load"><RotateCw :size="17"/>{{ resource.loading.value ? '检测中…' : '重新检测' }}</button></div><ErrorNotice :error="resource.error.value" retry @retry="resource.load"/>
  <div v-if="data" :class="['health-banner', { degraded: data.status !== 'ready' }]"><span class="health-icon"><Activity :size="28"/></span><div><h2>{{ data.status === 'ready' ? '核心依赖检查通过' : '检测到需要关注的异常' }}</h2><p>{{ data.status === 'ready' ? '数据库、处理队列与检索控制面已响应。未检测的服务不计为正常。' : '查看下方异常项，处理后重新检测。' }}</p></div><span class="small muted push-right">检测时间 {{ dateTime(data.checked_at) }}</span></div>
  <div v-if="resource.loading.value && !data" class="skeleton-card"/><div class="health-grid"><article v-for="item in items" :key="item.key" class="health-card"><div class="health-card-header"><component :is="item.icon" :size="22"/><span :class="['health-label', item.status]"><component :is="item.status === 'ready' ? Check : item.status === 'failed' ? CircleAlert : CircleHelp" :size="14"/>{{ labels[item.status] }}</span></div><h3>{{ item.name }}</h3><p>{{ item.detail }}</p></article></div>
  <div v-if="data?.visual_processing" class="visual-coverage"><h2>图片处理与调用记录</h2><p>识别：{{ data.visual_processing.ocr_model }} · 视觉复核：{{ data.visual_processing.verifier_model }} · 本地文字核对：{{ data.visual_processing.local_ocr }}</p><p>{{ data.visual_processing.account_limit_enabled ? `账号共享并发上限 ${data.visual_processing.account_concurrency}` : '账号共享限流未启用' }} · 页面渲染补充{{ data.visual_processing.render_fallback_enabled ? '已启用' : '未启用' }}</p><p v-if="data.model_usage">累计实际调用 {{ data.model_usage.call_count }} 次，缓存复用 {{ data.model_usage.cache_hits }} 次；{{ data.model_usage.input_tokens }} 输入 / {{ data.model_usage.output_tokens }} 输出 token。</p><p v-if="data.model_usage?.unpriced_calls" class="muted small">{{ data.model_usage.unpriced_calls }} 次调用未配置价格，费用未知；已知部分估算 ¥{{ Number(data.model_usage.known_cost_cny || 0).toFixed(4) }}。</p></div><div v-if="data?.degraded_reasons.length" class="notice warning"><CircleAlert :size="20"/><div><strong>异常记录</strong><p v-for="reason in data.degraded_reasons" :key="reason">{{ reasons[reason] || '依赖检测未通过，请查看技术详情。' }}</p></div></div><div class="workspace-note"><CircleHelp :size="20"/><div><strong>检测通过与业务验收分别记录</strong><span>模型状态来自本次服务运行的真实调用记录；文件处理与问答是否成功，以对应任务结果为准。</span></div></div><details v-if="data" class="technical"><summary>技术详情</summary><pre>{{ JSON.stringify(data, null, 2) }}</pre></details>
</div></template>
