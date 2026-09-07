<script setup>
import { computed, ref, watch, onUnmounted } from 'vue';
import { request } from '../api.js';
import ErrorNotice from './ErrorNotice.vue';
import EmptyState from './EmptyState.vue';
import VisualAsset from './VisualAsset.vue';
import VisualEditor from './VisualEditor.vue';
import SourceDocument from './SourceDocument.vue';
const props = defineProps({ versionId: String, documentId: String, filename: String, processing: Boolean });
const emit = defineEmits(['reparsed']);
const assets = ref([]), loading = ref(false), error = ref(null), active = ref(''), showDocument = ref(false), reparsing = ref(false), processingState = ref({}), usage = ref({}), editing = ref(false), excluding = ref(false), reason = ref(''), independent = ref(false), diff = ref(null), exclusionPreview = ref(null);
let revision = 0, controller, loadedVersion = '';
const current = computed(() => assets.value.find(a => a.id === active.value));
const coverage = computed(() => processingState.value.coverage || {});
const gaps = computed(() => (coverage.value.objects || []).filter(o => o.state === 'unprocessed'));
const labels = { queued:'等待处理', extracting:'识别中', verifying:'独立复核中', checking_text:'核对数字与位置', completed:'处理完成', needs_review:'需要复核', failed:'处理失败', excluded:'已排除' };
const statusLabels = { verified:'已确认', needs_review:'待复核', failed:'处理失败', excluded:'已排除', queued:'等待处理' };
const kinds = { shape:'可编辑图形', smartart:'SmartArt', chart:'图表', external_image:'外链图片', vector_graphics:'矢量图形', legacy_office:'旧格式图形', uninspected_xml:'未完成检查的内容' };
async function load() {
  const id = props.versionId, own = ++revision;
  controller?.abort(); controller = new AbortController(); error.value = null;
  const switched = loadedVersion !== id;
  if (switched) { assets.value = []; processingState.value = {}; usage.value = {}; active.value = sessionStorage.getItem(`visual-active.${id}`) || ''; editing.value = false; excluding.value = false; diff.value = null; showDocument.value = false; }
  if (!id) return;
  loading.value = switched || !assets.value.length;
  try {
    const result = await request(`/document-versions/${id}/visuals`, { signal: controller.signal });
    if (own !== revision || props.versionId !== id) return;
    loadedVersion = id; assets.value = result.items; processingState.value = result.processing || {}; usage.value = result.usage || {};
    if (!assets.value.some(a => a.id === active.value)) active.value = assets.value[0]?.id || '';
  } catch (cause) { if (own === revision && cause.name !== 'AbortError') error.value = cause; }
  finally { if (own === revision) loading.value = false; }
}
watch(() => [props.versionId, props.processing], load, { immediate: true });
watch(active, id => { if (id) sessionStorage.setItem(`visual-active.${props.versionId}`, id); editing.value = false; excluding.value = false; });
const timer = setInterval(() => { if (props.processing && !loading.value) load(); }, 4000);
onUnmounted(() => { clearInterval(timer); ++revision; controller?.abort(); });
async function submitRevision(body = null) {
  if (reparsing.value) return;
  const id = props.versionId, doc = props.documentId;
  reparsing.value = true; error.value = null;
  const storageKey = `ragkb.visual-reparse.${id}.${body ? 'edit' : 'all'}`;
  try {
    let attempt = JSON.parse(sessionStorage.getItem(storageKey) || 'null');
    if (!attempt || JSON.stringify(attempt.body) !== JSON.stringify(body)) { const state = await request(`/documents/${doc}/preview`); attempt = { key: crypto.randomUUID(), condition: String(state.row_version), body }; sessionStorage.setItem(storageKey, JSON.stringify(attempt)); }
    const result = await request(`/document-versions/${id}:${body ? 'revise-visuals' : 'reparse-visuals'}`, { method:'POST', headers:{ 'If-Match': attempt.condition, 'Idempotency-Key': attempt.key }, body: JSON.stringify(body || {}) });
    sessionStorage.removeItem(storageKey); if (props.versionId === id) { editing.value = false; excluding.value = false; emit('reparsed', result); }
  } catch (cause) { if ([409,412,422].includes(cause.status)) sessionStorage.removeItem(storageKey); if (props.versionId === id) error.value = cause; }
  finally { reparsing.value = false; }
}
function saveEdit({ extraction, reason }) { return submitRevision({ edits:[{ asset_id:active.value, extraction }], reason, confirmed_against_original:true }); }
async function beginExclusion() { excluding.value = !excluding.value; independent.value = false; exclusionPreview.value = null;
 if (!excluding.value) return; const version = props.versionId, id = active.value;
 try { const result = await request(`/document-versions/${version}/visual-exclusion-preview?asset_id=${id}`); if (props.versionId === version && active.value === id) exclusionPreview.value = result; } catch (cause) { error.value = cause; }
}
async function loadDiff() { try { diff.value = await request(`/document-versions/${props.versionId}/visual-diff?base_version_id=${encodeURIComponent(processingState.value.plan.base_version_id)}`); } catch (cause) { error.value = cause; } }
</script>
<template><section class="document-visuals"><div class="section-toolbar"><div><h2>原图与识别结果</h2><p class="small muted">保留原件，对照检查文字、表格和图中的关系。</p></div><div class="visual-actions"><button class="btn" @click="showDocument = !showDocument">{{ showDocument ? '收起原文档' : '展开原文档' }}</button><button class="btn" :disabled="reparsing || processing" @click="submitRevision()">{{ reparsing ? '正在提交…' : '重新识别为新版本' }}</button></div></div>
<p class="small muted">重新识别会保留当前版本，新版本经过复核发布后才参与问答。</p>
<div class="visual-coverage"><strong>内容覆盖</strong><p v-if="!coverage.inspection">此版本尚未完成图形覆盖检查，可重新识别后查看。</p><p v-else-if="!assets.length && !gaps.length">已检查支持的图形对象，未发现需要识别的图片。</p><p v-else>发现 {{ processingState.total_images ?? assets.length }} 张图片；{{ assets.filter(a=>a.status==='verified').length }} 张已确认，{{ assets.filter(a=>a.status==='excluded').length }} 张已排除。</p><p v-if="gaps.length" class="accent-warning">{{ gaps.length }} 个图形对象尚未覆盖，不能将本版本视为完整识别。</p><ul v-if="gaps.length"><li v-for="gap in gaps" :key="gap.id">{{ kinds[gap.kind] || '图形内容' }} · {{ gap.page ? `第 ${gap.page} 页` : gap.part }}<span v-if="gap.kind === 'external_image'">：请将原图嵌入文档后重新上传</span></li></ul><p v-if="coverage.missing_pages?.length">尚未处理页面：{{ coverage.missing_pages.join('、') }}</p><p v-if="coverage.render_error" class="small muted">页面渲染未完成，可查看技术详情或补充可读取的原图。</p><p v-if="processingState.plan?.exclude_sections?.length" class="accent-warning">当前版本仅包含保留内容，已排除：{{ processingState.plan.exclude_sections.join('、') }}</p><p v-if="usage.call_count != null" class="small muted">模型调用 {{ usage.call_count }} 次 · 缓存复用 {{ usage.cache_hits }} 次 · {{ usage.input_tokens }} 输入 / {{ usage.output_tokens }} 输出 token · {{ usage.unpriced_calls ? '部分调用未配置价格' : `估算 ¥${Number(usage.known_cost_cny || 0).toFixed(4)}` }}</p><p v-if="processingState.finished_at" class="small muted">图片处理耗时 {{ Math.max(0, processingState.finished_at - processingState.started_at).toFixed(1) }} 秒；模型请求累计耗时 {{ Number(usage.total_elapsed_seconds || 0).toFixed(1) }} 秒（并发请求分别计时）。</p></div>
<SourceDocument v-if="showDocument" :version-id="versionId" :filename="filename"/><ErrorNotice :error="error" retry @retry="load"/><div v-if="loading && !assets.length" class="skeleton-card"/><EmptyState v-else-if="!assets.length" title="此版本尚无图片识别记录" description="请结合上方覆盖检查区分纯文字、尚未检查与未覆盖内容。"/>
<nav v-if="assets.length" class="visual-gallery" aria-label="文档图片导航"><button v-for="(asset,i) in assets" :key="asset.id" :class="{ active: active === asset.id }" @click="active = asset.id">图片 {{ i + 1 }}<span :class="['stage-dot', asset.status === 'verified' ? 'success' : 'running']"/><small>{{ labels[asset.stage] || (asset.status === 'verified' ? '处理完成' : '待复核') }}</small></button></nav>
<template v-if="current"><div class="visual-actions"><button class="btn small-button" :disabled="reparsing || processing || current.status === 'excluded'" @click="submitRevision({ retry_assets:[active], reason:'单图重新识别' })">仅重试这张图</button><button v-if="current.extraction" class="btn small-button" :disabled="reparsing || processing" @click="editing = !editing">修订这张图</button><button class="btn small-button" :disabled="reparsing || processing" @click="beginExclusion">排除相关章节</button><button v-if="processingState.plan?.base_version_id" class="btn small-button" @click="loadDiff">查看版本变化</button></div>
<div v-if="excluding" class="visual-editor"><h3>排除不完整内容后生成新版本</h3><p>将排除这张图所在的“{{ current.section_path || '未分章内容' }}”，以及明确引用它的章节，包括其中的正文、表头、条件和父块。</p><p v-if="exclusionPreview" class="accent-warning">本次完整排除范围：{{ exclusionPreview.sections.join('、') }} · {{ exclusionPreview.asset_ids.length }} 张图片</p><p v-else>正在核对关联范围…</p><label class="field-label">排除原因<textarea v-model="reason" rows="2"/></label><label class="check-row"><input v-model="independent" type="checkbox"/>我已检查其余章节可独立使用；不会将剩余内容当作完整手册或完整流程。</label><button class="btn" :disabled="!exclusionPreview || !reason.trim() || !independent || reparsing" @click="submitRevision({ exclude_assets:[active], reason, confirm_independent_sections:true })">生成保留其余内容的新版本</button></div>
<VisualAsset :key="current.id" :asset="current" :view-key="`${versionId}:${current.id}`"/><VisualEditor v-if="editing" :asset="current" :busy="reparsing" @cancel="editing = false" @save="saveEdit"/>
<div v-if="diff" class="visual-editor"><h3>与来源版本的变化</h3><div v-for="item in diff.items" :key="item.asset_id"><p class="small muted">{{ statusLabels[item.before_status] || '无记录' }} → {{ statusLabels[item.after_status] || '无记录' }} · {{ item.changes.length }} 处变化</p><div v-for="(change,i) in item.changes" :key="i" class="revision-change"><span>{{ change.label || '内容' }}</span><del>{{ change.before }}</del><ins>{{ change.after }}</ins></div></div><button class="btn" @click="diff=null">收起变化</button></div>
</template></section></template>
