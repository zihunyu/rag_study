<script setup>
import { computed, ref, watch } from 'vue';
import { request, command } from '../api.js';
import ErrorNotice from './ErrorNotice.vue';
import OriginalFilePreview from './OriginalFilePreview.vue';
import MarkdownContent from './MarkdownContent.vue';
const props = defineProps({ space: String, documents: Array });
const documentId = ref(''), versionId = ref(''), versions = ref([]), pages = ref('1'), snapshot = ref(null);
const standards = ref([]), runs = ref([]), selected = ref(''), editor = ref(null), revision = ref(0), history = ref([]);
const busy = ref(false), error = ref(null), message = ref(''), result = ref(null), baseline = ref(''), comparison = ref(null), showChunks = ref(false);
const base = computed(() => `/spaces/${props.space}/acceptance/parsing`);
const labels = { found: '原句已找到', not_found: '原句未找到', unrecorded: '未记录', passed: '样本检查通过', failed: '存在差异', incomplete: '记录不完整', improved: '改善', regressed: '退化', unchanged: '不变', pending: '待确认' };
const visibleStandards = computed(() => standards.value.filter(s => s.payload.document_id === documentId.value));
const visibleRuns = computed(() => runs.value.filter(r => r.payload.standard?.document_id === documentId.value));
const unsaved = computed(() => {
  const saved = standards.value.find(s => s.id === selected.value);
  if (!saved || !editor.value) return true;
  const { original_sha256, ...spec } = saved.payload;
  return JSON.stringify(spec) !== JSON.stringify(editor.value);
});
const origins = { parser: '解析正文', visual: '图片补充', mixed: '含图片的组合片段' };
let epoch = 0;
async function act(fn) { if (busy.value) return; busy.value = true; error.value = null; message.value = ''; try { await fn(); } catch (cause) { error.value = cause; } finally { busy.value = false; } }
async function load() { [standards.value, runs.value] = await Promise.all([request(`${base.value}/standards`), request(`${base.value}/runs`)]); }
function newStandard() {
  selected.value = ''; revision.value = 0; history.value = [];
  editor.value = { key: `解析-${crypto.randomUUID().slice(0, 8)}`, document_id: documentId.value, reference_version_id: versionId.value, checks: [], original_checked: false, note: '' };
}
function addCheck() { editor.value.checks.push({ id: crypto.randomUUID(), label: '', kind: 'text', pages: pages.value.split(',').map(p => Number(p.trim())), quote: '' }); }
function chooseStandard() {
  const item = standards.value.find(s => s.id === selected.value);
  if (!item) { newStandard(); return; }
  const { original_sha256, ...spec } = item.payload;
  editor.value = JSON.parse(JSON.stringify(spec)); revision.value = item.revision;
  pages.value = [...new Set(spec.checks.flatMap(c => c.pages))].join(',');
}
async function inspect() { snapshot.value = await request(`${base.value}/versions/${versionId.value}?pages=${encodeURIComponent(pages.value)}`); }
async function save() {
  const data = await command(`${base.value}/standards`, { standard: editor.value, revision: revision.value });
  await load(); selected.value = data.id; revision.value = data.revision;
  message.value = '已保存独立原文件标准。未把解析结果自动当成标准。';
}
async function run() { if (unsaved.value) return; result.value = await command(`${base.value}/runs`, { standard_id: selected.value, version_id: versionId.value }); await load(); comparison.value = null; }
async function compare() { comparison.value = await request(`${base.value}/compare?baseline=${baseline.value}&candidate=${result.value.id}`); }
watch(documentId, async () => {
  const own = ++epoch; versions.value = []; versionId.value = ''; snapshot.value = null; editor.value = null; result.value = null; comparison.value = null;
  if (!documentId.value) return;
  try { const data = await request(`/documents/${documentId.value}/versions/preview`); if (own !== epoch) return; versions.value = data; versionId.value = data[0]?.id || ''; newStandard(); } catch (cause) { if (own === epoch) error.value = cause; }
});
watch(versionId, () => { snapshot.value = null; if (editor.value && !selected.value) editor.value.reference_version_id = versionId.value; });
watch(() => props.space, async () => { ++epoch; documentId.value = ''; await act(load); }, { immediate: true });
</script>
<template><section class="parsing-acceptance">
  <div class="panel"><h2>文档解析验收</h2><p>对照原文件建立代表性页面标准，再检查解析和分块是否保留。样本通过不代表整份文件全部正确。没有固定页码的文本、Word 或工作表，填 1 查看位置片段。</p>
    <div class="acceptance-toolbar"><label>文件<select v-model="documentId" aria-label="解析验收文件" :disabled="busy"><option value="">选择文件</option><option v-for="d in documents" :key="d.document_id" :value="d.document_id">{{ d.filename }}</option></select></label>
      <label>待检查版本<select v-model="versionId" aria-label="解析验收版本" :disabled="busy"><option v-for="v in versions" :key="v.id" :value="v.id">版本 {{ v.version_no }} · {{ v.processing_state }}</option></select></label>
      <label>页码 / 幻灯片（英文逗号分隔）<input v-model="pages" aria-label="验收页码" placeholder="9,10"></label><button class="btn" :disabled="busy || !versionId" @click="act(inspect)">并排查看原文件与解析</button>
    </div><ErrorNotice :error="error"/><p v-if="message" role="status">{{ message }}</p>
  </div>
  <div v-if="snapshot" class="parsing-split"><OriginalFilePreview :key="versionId" :version-id="versionId" :mime="snapshot.mime_type" :page="Number(pages.split(',')[0]) || 1"/>
    <section class="panel parsing-text"><h3>解析与分块</h3><p>版本 {{ snapshot.version_id }} · {{ snapshot.parser_revision }}</p><label><input v-model="showChunks" type="checkbox">查看分块结果</label>
      <p v-if="!showChunks && snapshot.parsed === null">此版本未保留解析原始快照，不能用分块冒充解析结果。重新解析可建立记录。</p>
      <p v-if="showChunks && snapshot.chunks === null">{{ snapshot.chunks_note || '分块尚未准备完成。' }}</p>
      <details v-for="row in (showChunks ? snapshot.chunks : snapshot.parsed) || []" :key="row.id" open><summary>{{ origins[row.origin] || row.type || row.kind }} · {{ row.locator.page ? `第 ${row.locator.page} 页` : JSON.stringify(row.locator) }}</summary><template v-if="(row.type || row.kind) === 'table'"><MarkdownContent :text="row.text" source-tables/><details><summary>查看解析原始文本</summary><pre>{{ row.text }}</pre></details></template><pre v-else>{{ row.text }}</pre><small>{{ row.id }}</small></details>
    </section>
  </div>
  <section v-if="editor" class="panel"><h3>独立原文件标准</h3><p>从左侧原文件核对并填写摘录，保留数字、单位、条件和例外。表格行填写同一行的连续文字；不要仅填写散落的关键词。</p>
    <label>已有标准<select v-model="selected" aria-label="原文件标准" :disabled="busy" @change="chooseStandard"><option value="">新建标准</option><option v-for="s in visibleStandards" :key="s.id" :value="s.id">{{ s.payload.key }} · v{{ s.revision }} · {{ s.payload.original_checked ? '已核对' : '待核对' }}</option></select></label>
    <label>标准编号<input v-model="editor.key" :disabled="!!selected" aria-label="解析标准编号"></label><small>原文件依据版本：{{ editor.reference_version_id }}</small>
    <div v-for="(check, index) in editor.checks" :key="check.id" class="criterion-card"><strong>标准 {{ index + 1 }} · 页 {{ check.pages.join('、') }}</strong><label>内容类型<select v-model="check.kind"><option value="text">文字摘录</option><option value="list_item">清单条目</option><option value="table_row">表格行与单位</option><option value="condition">事实、条件与例外</option></select></label><label>要检查什么<input v-model="check.label" :aria-label="`解析标准 ${index + 1} 说明`"></label><label>原文件连续摘录<textarea v-model="check.quote" :aria-label="`解析标准 ${index + 1} 原文`" rows="3"/></label><button class="text-button" @click="editor.checks.splice(index, 1)">删除此项</button></div>
    <button class="btn" :disabled="editor.checks.length >= 100" @click="addCheck">添加页面标准</button><label>核对说明<textarea v-model="editor.note" aria-label="原文件核对说明"/></label><label><input v-model="editor.original_checked" type="checkbox" aria-label="已核对原文件">我已对照原文件核对这些标准</label>
    <div class="acceptance-toolbar"><button class="btn primary" :disabled="busy || !editor.checks.length" @click="act(save)">保存原文件标准</button><button class="btn" :disabled="busy || !selected" @click="act(async () => { history = await request(`${base}/standards/${selected}/revisions`); })">标准版本记录</button><button class="btn" :disabled="busy || !selected || !editor.original_checked || unsaved" @click="act(run)">检查所选解析版本</button><small v-if="selected && unsaved">请先保存标准修改，再执行检查。</small></div><details v-for="h in history" :key="h.id"><summary>v{{ h.revision }} · {{ h.actor_id }}</summary><pre>{{ JSON.stringify(h.payload, null, 2) }}</pre></details>
  </section>
  <section v-if="visibleRuns.length" class="panel"><h3>解析验收记录</h3><button v-for="r in visibleRuns" :key="r.id" class="btn" @click="result = r; comparison = null">{{ r.payload.standard.key }} · {{ new Date(r.updated_at * 1000).toLocaleString() }} · {{ labels[r.payload.verdict] }}</button></section>
  <section v-if="result" class="panel"><h3>{{ labels[result.payload.verdict] }}</h3><p>只检查已确认样本的原句保留；忽略排版空白，保留数字、符号和单位差异。原句未找到需要对照原文件定位。</p>
    <table><thead><tr><th>页面标准</th><th>解析</th><th>分块</th></tr></thead><tbody><tr v-for="r in result.payload.rows" :key="r.id"><td>{{ r.label }}<details><summary>原文件标准</summary><p>{{ r.quote }}</p></details></td><td v-for="stage in ['parsed','chunks']" :key="stage">{{ labels[r.stages[stage].status] }}<details v-if="r.stages[stage].matches.length"><summary>位置</summary><pre>{{ JSON.stringify(r.stages[stage].matches, null, 2) }}</pre></details></td></tr></tbody></table>
    <div class="acceptance-toolbar"><label>基准记录<select v-model="baseline" aria-label="解析比较基准"><option value="">选择基准</option><option v-for="r in visibleRuns.filter(r => r.id !== result.id)" :key="r.id" :value="r.id">{{ r.payload.standard.key }} · {{ r.payload.version_id }} · {{ labels[r.payload.verdict] }}</option></select></label><button class="btn" :disabled="busy || !baseline" @click="act(compare)">比较解析版本</button></div>
    <template v-if="comparison"><p v-if="!comparison.comparable">{{ comparison.reason }}</p><table v-else><thead><tr><th>标准</th><th>阶段</th><th>基准 → 本次</th><th>变化</th></tr></thead><tbody><tr v-for="r in comparison.rows" :key="r.id + r.stage"><td>{{ r.label }}</td><td>{{ r.stage === 'parsed' ? '解析' : '分块' }}</td><td>{{ labels[r.before] }} → {{ labels[r.after] }}</td><td>{{ labels[r.change] }}</td></tr></tbody></table><p>本地检查耗时 {{ comparison.before_seconds }} → {{ comparison.after_seconds }} 秒；这是验收计算时间，不能据此宣称解析器提速。</p></template>
  </section>
</section></template>
