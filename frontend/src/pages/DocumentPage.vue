<script setup>
import { computed, ref, watch, onUnmounted, nextTick } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { ArrowLeft, FileText, Download, Upload, MoreHorizontal, Check, ChevronRight, BookOpen, Layers, History, ShieldCheck, RotateCcw, Ban, Trash2, ExternalLink } from '@lucide/vue';
import { useResource } from '../composables/useResource.js';
import { request, requestPage, apiUrl, queryString, confirmPublication, jobCommand } from '../api.js';
import { useWorkspace } from '../stores/workspace.js';
import { dateTime, fileSize, locationLabel } from '../format.js';
import StatusBadge from '../components/StatusBadge.vue';
import ErrorNotice from '../components/ErrorNotice.vue';
import EmptyState from '../components/EmptyState.vue';
import UploadDialog from '../components/UploadDialog.vue';
import AppDialog from '../components/AppDialog.vue';
import MarkdownContent from '../components/MarkdownContent.vue';
import DocumentVisuals from '../components/DocumentVisuals.vue';
const route = useRoute(), router = useRouter(), store = useWorkspace();
const spaceId = computed(() => route.params.spaceId), documentId = computed(() => route.params.documentId);
const resource = useResource(signal => request(`/spaces/${spaceId.value}/documents/${documentId.value}/workspace`, { signal }));
const doc = resource.data;
const versionId = ref(''), section = ref('quality'), activeChunk = ref(''), menuOpen = ref(false), uploading = ref(false), action = ref(null), busy = ref(false), actionError = ref(null), publicationPhase = ref(null);
const chunks = ref([]), cursor = ref(null), chunkLoading = ref(false), chunkError = ref(null), quality = ref(null);
let revision = 0, timer;
const version = computed(() => doc.value?.versions.find(v => v.id === versionId.value));
const canPublish = computed(() => doc.value?.available_actions.includes('review_publish') && versionId.value === doc.value.version_id && quality.value && quality.value.disposition !== 'BLOCKED_REAL_VALIDATION');
async function readVersion() {
  const own = ++revision, id = versionId.value;
  chunks.value = []; cursor.value = null; chunkError.value = null; quality.value = null;
  if (!id) return;
  chunkLoading.value = true;
  const results = await Promise.allSettled([requestPage(`/document-versions/${id}/chunks/preview?limit=100`), request(`/document-versions/${id}/quality-report`)]);
  if (own !== revision) return;
  if (results[0].status === 'fulfilled') { chunks.value = results[0].value.items; cursor.value = results[0].value.nextCursor; }
  else chunkError.value = results[0].reason;
  if (results[1].status === 'fulfilled') quality.value = results[1].value;
  chunkLoading.value = false;
  if (route.query.chunk) {
    const target = String(route.query.chunk); section.value = 'content';
    while (own === revision && cursor.value && !chunks.value.some(chunk => chunk.chunk_id === target) && !chunkError.value) await moreChunks();
    if (own !== revision) return;
    await nextTick();
    if (chunks.value.some(chunk => chunk.chunk_id === target)) locate(target);
    else chunkError.value ||= new Error('SOURCE_CHUNK_NOT_FOUND');
  }
}
async function moreChunks() {
  const own = revision; chunkLoading.value = true;
  try { const result = await requestPage(`/document-versions/${versionId.value}/chunks/preview?${queryString({ limit: 100, cursor: cursor.value })}`); if (own === revision) { chunks.value.push(...result.items); cursor.value = result.nextCursor; } }
  catch (cause) { if (own === revision) chunkError.value = cause; } finally { if (own === revision) chunkLoading.value = false; }
}
function locate(id) { activeChunk.value = id; document.getElementById(`chunk-${id}`)?.scrollIntoView({ behavior: 'smooth', block: 'center' }); }
async function load() { const previousLatest = doc.value?.version_id; const data = await resource.load(); if (data) publicationPhase.value = data.publication?.phase || null; if (data && (!data.versions.some(v => v.id === versionId.value) || versionId.value === previousLatest)) versionId.value = data.versions.some(v => v.id === route.query.version) ? String(route.query.version) : data.version_id; }
watch(versionId, readVersion);
watch(() => [route.query.version, route.query.chunk], () => { if (doc.value?.versions.some(v => v.id === route.query.version) && versionId.value !== route.query.version) versionId.value = String(route.query.version); else if (versionId.value) readVersion(); });
watch(documentId, () => { ++revision; resource.clear(); versionId.value = ''; action.value = null; menuOpen.value = false; actionError.value = null; publicationPhase.value = null; section.value = route.query.chunk ? 'content' : 'quality'; uploading.value = false; load(); }, { immediate: true });
timer = setInterval(async () => { if (['DRAFT','PROCESSING'].includes(doc.value?.processing_state) && !busy.value) { const previous = doc.value.processing_state; await load(); if (doc.value?.processing_state !== previous) readVersion(); } }, 4000);
onUnmounted(() => { ++revision; clearInterval(timer); });
async function execute() {
  if (busy.value) return;
  const target = { documentId: documentId.value, versionId: versionId.value, spaceId: spaceId.value, action: action.value };
  const stillCurrent = () => documentId.value === target.documentId && spaceId.value === target.spaceId;
  busy.value = true; actionError.value = null;
  try {
    if (target.action === 'publish') {
      const result = await confirmPublication(target.versionId, '已检查解析内容与质量报告，确认发布');
      if (!stillCurrent()) { await store.refresh(); return; }
      publicationPhase.value = result.phase;
      if (result.phase !== 'published') { actionError.value = new Error('REVIEW_PASSED_PUBLICATION_FAILED'); return; }
    } else {
      const snapshot = await request(`/documents/${target.documentId}/lifecycle`);
      await request(target.action === 'delete' ? `/documents/${target.documentId}` : `/documents/${target.documentId}:${target.action}`, {
        method: target.action === 'delete' ? 'DELETE' : 'POST',
        headers: { 'If-Match': String(snapshot.row_version), 'Idempotency-Key': crypto.randomUUID() },
        ...(target.action === 'rollback' ? { body: JSON.stringify({ version_id: target.versionId }) } : {}),
      });
    }
    if (!stillCurrent()) { await store.refresh(); return; }
    if (target.action === 'delete') { action.value = null; await store.refresh(); if (stillCurrent()) await router.push(`/knowledge-bases/${target.spaceId}/documents`); return; }
    action.value = null; await Promise.all([load(), store.refresh()]); await readVersion();
  } catch (cause) { if (stillCurrent()) actionError.value = cause; } finally { busy.value = false; }
}
async function retry() { busy.value = true; actionError.value = null; try { await jobCommand(doc.value.job_id, 'retry'); await load(); } catch (cause) { actionError.value = cause; } finally { busy.value = false; } }
const actionLabels = { publish: '确认发布', revoke: '撤回文档', delete: '删除文档', rollback: '回滚到此版本' };
const issueLabels = { LOCATOR_MISSING: '部分内容缺少来源位置', EMPTY_DOCUMENT: '没有提取到有效内容', LOW_LOCATOR_COVERAGE: '来源位置覆盖率不足', LOCAL_PLACEHOLDER: '本地解析尚未经过真实服务验收' };
</script>
<template><div class="page document-page">
  <RouterLink class="back-link" :to="`/knowledge-bases/${spaceId}/documents`"><ArrowLeft :size="16"/>返回文档列表</RouterLink>
  <ErrorNotice :error="resource.error.value" retry @retry="load"/>
  <div v-if="resource.loading.value && !doc" class="skeleton-card"/>
  <template v-if="doc"><div class="page-heading compact"><div class="document-heading"><span class="library-symbol"><FileText :size="25"/></span><div><h1>{{ version?.original_key?.split('/').pop() || doc.filename }}</h1><p class="page-description">{{ fileSize(doc.size_bytes) }}<span class="separator">·</span>版本 {{ version?.version_no || doc.version_no }}<span class="separator">·</span>{{ dateTime(doc.updated_at) }}</p></div></div><div class="toolbar-actions"><a class="btn" :href="apiUrl(`/document-versions/${versionId}/original/preview`)" download><Download :size="16"/>原文件</a><div class="dropdown"><button class="icon-button" aria-label="文档操作" :aria-expanded="menuOpen" @click="menuOpen = !menuOpen"><MoreHorizontal :size="21"/></button><div v-if="menuOpen" class="dropdown-menu"><button :disabled="!doc.available_actions.includes('upload_version')" @click="uploading = true; menuOpen = false"><Upload :size="16"/>上传新版本</button><button :disabled="!doc.available_actions.includes('revoke')" @click="action = 'revoke'; menuOpen = false"><Ban :size="16"/>撤回文档</button><button class="danger-text" :disabled="!doc.available_actions.includes('delete')" @click="action = 'delete'; menuOpen = false"><Trash2 :size="16"/>删除文档</button></div></div></div></div>
  <RouterLink v-if="doc.available_actions.includes('ask')" class="btn" :to="{ path:'/chat', query:{ space:spaceId, document:documentId, mode:'overview' } }">逐章总结本篇</RouterLink><div class="document-status-strip"><StatusBadge :status="doc.availability"/><StatusBadge :status="doc.processing_state" kind="processing"/><span v-for="reason in doc.unavailability_reasons" :key="reason" class="muted small">{{ reason }}</span><RouterLink v-if="doc.job_id" to="/tasks" class="text-link push-right">查看处理任务<ExternalLink :size="13"/></RouterLink></div>
  <ErrorNotice :error="actionError"/><div v-if="publicationPhase === 'reviewed'" class="notice warning"><ShieldCheck :size="19"/><div><strong>复核已通过，发布尚未完成</strong><p>检查质量页面中点击“继续发布”，将从未完成的发布步骤重试。</p></div></div>
  <div v-if="doc.availability === 'failed' || doc.availability === 'cancelled'" class="notice warning"><div><strong>{{ doc.availability === 'failed' ? '文件处理未完成' : '处理已取消' }}</strong><p>可在任务中心查看处理原因并重试。</p><details v-if="doc.error_code" class="technical"><summary>技术详情</summary><code>{{ doc.error_code }}</code></details></div><button v-if="doc.available_actions.includes('retry')" class="btn" :disabled="busy" @click="retry"><RotateCcw :size="15"/>重新处理</button></div>
  <div class="document-layout"><aside class="document-navigation"><div class="field-label">文档版本</div><select v-model="versionId" aria-label="文档版本"><option v-for="item in doc.versions" :key="item.id" :value="item.id">版本 {{ item.version_no }}{{ item.id === doc.current_version_id ? ' · 当前发布' : '' }}</option></select><nav aria-label="文档内容"><button :class="{ selected: section === 'quality' }" @click="section = 'quality'"><ShieldCheck :size="17"/>质量检查</button><button :class="{ selected: section === 'content' }" @click="section = 'content'"><BookOpen :size="17"/>解析内容<span>{{ chunks.length }}{{ cursor ? '+' : '' }}</span></button><button :class="{ selected: section === 'visuals' }" @click="section = 'visuals'"><Layers :size="17"/>原图与识别</button><button :class="{ selected: section === 'history' }" @click="section = 'history'"><History :size="17"/>版本历史</button></nav><div v-if="section === 'content' && chunks.length" class="chunk-navigation"><p class="nav-caption">内容导航</p><button v-for="(chunk, i) in chunks" :key="chunk.chunk_id" :class="{ active: activeChunk === chunk.chunk_id }" @click="locate(chunk.chunk_id)"><span>{{ String(i + 1).padStart(2, '0') }}</span>{{ locationLabel(chunk.locator) || `分块 ${i + 1}` }}</button></div></aside>
  <div class="document-main"><template v-if="section === 'quality'"><section class="panel quality-panel"><div class="panel-heading"><div><p class="eyebrow">QUALITY REVIEW</p><h2>发布前，检查你的知识</h2><p class="muted">确认解析内容完整，来源位置准确后，再用于知识问答。</p></div><ShieldCheck :size="28" class="accent"/></div><template v-if="quality"><div class="quality-metrics"><div><span>解析内容节点</span><strong>{{ quality.node_count }}</strong></div><div><span>来源位置覆盖率</span><strong>{{ Math.round(quality.locator_coverage * 100) }}<small>%</small></strong></div><div><span>质量问题</span><strong>{{ quality.issue_codes.length }}</strong></div></div><div :class="['notice', quality.disposition === 'BLOCKED_REAL_VALIDATION' ? 'warning' : 'success']"><Check :size="19"/><div><strong>{{ quality.disposition === 'BLOCKED_REAL_VALIDATION' ? '需要处理质量问题后再发布' : quality.issue_codes.length ? '解析完成，请检查以下问题' : '解析完成，可以进行人工确认' }}</strong><p>版本 {{ version?.version_no }} · {{ quality.source_format?.toUpperCase() }} 文档</p></div></div><ul v-if="quality.issue_codes.length" class="issue-list"><li v-for="issue in quality.issue_codes" :key="issue"><span>{{ issue.startsWith('VISUAL_') ? '图片需要对照原图复核，请打开「原图与识别」' : issueLabels[issue] || '解析器报告了需要检查的项目' }}</span><details><summary>技术详情</summary><code>{{ issue }}</code></details></li></ul><p v-if="quality.issue_codes.length" class="small muted">当前质量报告未提供逐项问题坐标，请结合解析内容核对；不会生成推测的位置。</p><div class="panel-footer"><button class="btn" @click="section = 'content'"><BookOpen :size="16"/>阅读解析内容</button><button v-if="canPublish" class="btn primary" @click="action = 'publish'"><Check :size="16"/>{{ publicationPhase === 'reviewed' ? '继续发布' : '确认发布' }}</button><StatusBadge v-else-if="versionId === doc.current_version_id && doc.is_answerable" status="available"/></div></template><EmptyState v-else title="质量报告尚未生成" description="文件完成解析后，质量摘要会显示在这里。"/></section></template>
  <template v-else-if="section === 'content'"><div class="section-toolbar"><div><h2>解析内容</h2><p class="small muted">按实际分块顺序展示；位置来自原文件的解析结果。</p></div><span class="count-pill">{{ chunks.length }}{{ cursor ? '+' : '' }} 个分块</span></div><ErrorNotice :error="chunkError" retry @retry="readVersion"/><div v-if="chunkLoading && !chunks.length" class="skeleton-card"/><EmptyState v-else-if="!chunks.length" title="还没有解析内容" description="文件处理完成后，可在这里阅读内容与检查分块。"/><article v-for="(chunk, i) in chunks" :id="`chunk-${chunk.chunk_id}`" :key="chunk.chunk_id" :class="['content-chunk', { focused: activeChunk === chunk.chunk_id }]"><div class="chunk-header"><span class="mono muted">{{ String(i + 1).padStart(2, '0') }}</span><span>{{ locationLabel(chunk.locator) }}</span><span class="push-right small muted">{{ chunk.is_parent ? '章节上下文' : '检索分块' }}{{ chunk.token_count ? ` · ${chunk.token_count} tokens` : '' }}</span></div><MarkdownContent :text="chunk.text" source-tables/><details class="technical"><summary>技术详情</summary><pre>{{ JSON.stringify({ chunk_id: chunk.chunk_id, locator: chunk.locator, status: chunk.status, vector_indexed: chunk.vector_indexed }, null, 2) }}</pre></details></article><button v-if="cursor" class="btn load-more" :disabled="chunkLoading" @click="moreChunks">{{ chunkLoading ? '加载中…' : '加载更多内容' }}</button></template>
  <DocumentVisuals v-else-if="section === 'visuals'" :version-id="versionId" :document-id="documentId" :filename="doc.filename" :processing="['DRAFT','PROCESSING'].includes(version?.processing_state)" @reparsed="async result => { await load(); versionId = result.document_version_id; section = 'visuals'; }"/><section v-else class="panel"><div class="panel-heading"><div><h2>版本历史</h2><p class="muted">每次更新都保留独立版本，便于追溯和回滚。</p></div><History :size="23" class="muted"/></div><div v-for="item in doc.versions" :key="item.id" class="version-row"><span class="version-mark">v{{ item.version_no }}</span><div><strong>版本 {{ item.version_no }}</strong><p class="small muted">{{ item.id === doc.current_version_id ? '当前发布版本' : item.id === doc.version_id ? '最新上传版本' : '历史版本' }}</p><StatusBadge :status="item.processing_state" kind="processing"/></div><button class="btn push-right" @click="versionId = item.id; section = 'content'">查看内容</button><button v-if="doc.lifecycle?.version_history?.includes(item.id) && item.id !== doc.current_version_id && doc.availability !== 'deleted'" class="btn" @click="versionId = item.id; action = 'rollback'"><RotateCcw :size="15"/>回滚</button></div></section>
  <details class="technical"><summary>文档技术详情</summary><pre>{{ JSON.stringify(doc, null, 2) }}</pre></details></div></div>
  <UploadDialog :open="uploading" :space-id="spaceId" :document-id="documentId" @close="uploading = false; load()"/>
  <AppDialog :open="!!action" :title="actionLabels[action] || '文档操作'" @close="!busy && (action = null)"><p v-if="action === 'publish'">已检查「{{ doc.filename }}」版本 {{ version?.version_no }} 的质量和解析内容？发布后，这个版本会成为当前问答的知识来源。</p><p v-else-if="action === 'delete'">删除「{{ doc.filename }}」后，它将从检索与问答中移除，关联引用会失效。</p><p v-else-if="action === 'revoke'">撤回「{{ doc.filename }}」后，它将停止参与问答，文档与版本记录会保留。</p><p v-else>将当前知识来源切换为版本 {{ version?.version_no }}，之后的提问会重新检索此版本。</p><ErrorNotice :error="actionError"/><div v-if="publicationPhase === 'reviewed'" class="notice warning"><ShieldCheck :size="19"/><div><strong>复核已通过，发布尚未完成</strong><p>检查质量页面中点击“继续发布”，将从未完成的发布步骤重试。</p></div></div><div class="dialog-actions"><button class="btn" :disabled="busy" @click="action = null">取消</button><button :class="['btn', action === 'delete' ? 'danger' : 'primary']" :disabled="busy" @click="execute">{{ busy ? '正在执行…' : actionLabels[action] }}</button></div></AppDialog>
  </template>
</div></template>
