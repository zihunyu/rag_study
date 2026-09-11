<script setup>
import { onMounted, ref } from 'vue';
import AppDialog from './AppDialog.vue';
import AcceptanceSourcePicker from './AcceptanceSourcePicker.vue';
import ErrorNotice from './ErrorNotice.vue';
import { request } from '../api.js';
const props = defineProps({ editor: Object, documents: Array, space: String, busy: Boolean, error: Object });
const emit = defineEmits(['save', 'close']);
const value = ref(structuredClone(JSON.parse(JSON.stringify(props.editor.case))));
const legacy = !value.value.criteria?.length && !!(value.value.required_points?.length || value.value.forbidden_claims?.length);
value.value.criteria ||= [];
if (legacy) value.value.criteria = [
  ...(value.value.required_points || []).map((text, i) => ({ id: `required-${i + 1}`, text, kind: 'required', sources: [] })),
  ...(value.value.forbidden_claims || []).map((text, i) => ({ id: `forbidden-${i + 1}`, text, kind: 'forbidden', sources: [] })),
];
value.value.list_checks ||= { expected_count: null, sequential: false, no_duplicates: false };
const history = ref(JSON.stringify(value.value.history || [], null, 2)), picking = ref(null), localError = ref(null);
const revisions = ref([]);
const originalStandards = ref([]);
onMounted(async () => { try { const rows = await request(`/spaces/${props.space}/acceptance/parsing/standards`); originalStandards.value = Array.isArray(rows) ? rows.filter(s => s.payload.original_checked) : []; } catch (cause) { localError.value = cause; } });
const checksFor = p => originalStandards.value.find(s => s.id === p.original_standard_id)?.payload.checks || [];
async function loadRevisions() { try { revisions.value = await request(`/spaces/${props.space}/acceptance/cases/${props.editor.id}/revisions`); } catch (cause) { localError.value = cause; } }
const name = id => props.documents.find(d => d.document_id === id)?.filename || id;
function addPoint() { value.value.criteria.push({ id: crypto.randomUUID(), text: '', kind: 'required', sources: [] }); }
function attach(source) { picking.value.sources.push(source); picking.value = null; }
function save() {
  localError.value = null;
  try {
    const data = JSON.parse(JSON.stringify(value.value));
    data.history = JSON.parse(history.value || '[]');
    if (!data.key.trim()) data.key = `Q-${crypto.randomUUID().slice(0, 8)}`;
    if (data.list_checks.expected_count === '') data.list_checks.expected_count = null;
    if (legacy && data.criteria.every(p => !p.sources.length) && data.criteria.every(p => p.kind !== 'unknown')) {
      data.required_points = data.criteria.filter(p => p.kind === 'required').map(p => p.text);
      data.forbidden_claims = data.criteria.filter(p => p.kind === 'forbidden').map(p => p.text);
      data.criteria = [];
    } else { data.required_points = []; data.forbidden_claims = []; }
    if (!data.source_notes.trim() && data.criteria.some(p => p.sources.length)) data.source_notes = '原文依据已逐项关联，请查看各要点。';
    emit('save', data);
  } catch (cause) { localError.value = cause; }
}
</script>
<template><AppDialog :open="true" role="dialog" :title="editor.id ? '编辑验收案例' : '新建验收案例'" class="acceptance-dialog" @close="emit('close')">
  <form @submit.prevent="save">
    <label>问题<textarea v-model="value.question" required maxlength="4000" rows="3" aria-label="案例问题"/></label>
    <label>预期行为<select v-model="value.expected_status" aria-label="案例预期行为"><option value="answered">回答问题（可在要点中要求说明未知部分）</option><option value="insufficient_evidence">资料不足时拒答</option><option value="conflicting_evidence">冲突时拒答</option><option value="needs_clarification">需要澄清</option><option value="out_of_scope">超出范围</option></select></label>
    <section><h3>验收要点与原文依据</h3><p class="small muted">每项描述一个事实或条件，可以使用不同措辞回答。原文仅供验收，不送给被测问答。</p>
      <div v-for="(point, i) in value.criteria" :key="point.id" class="criterion-card">
        <div class="acceptance-toolbar"><strong>要点 {{ i + 1 }}</strong><button type="button" class="text-button" @click="value.criteria.splice(i, 1)">删除要点</button></div>
        <select v-model="point.kind" :aria-label="`要点 ${i + 1} 类型`"><option value="required">必须覆盖</option><option value="forbidden">禁止说法</option><option value="unknown">必须说明未知</option></select>
        <textarea v-model="point.text" required maxlength="2000" :aria-label="`要点 ${i + 1}`" rows="2"/>
        <div v-for="(source, j) in point.sources" :key="source.chunk_id + j" class="source-binding"><strong>{{ name(source.document_id) }}</strong><small>版本 {{ source.version_id }}</small><textarea v-model="source.quote" required maxlength="4000" rows="3" :aria-label="`要点 ${i + 1} 原文 ${j + 1}`"/><button type="button" class="text-button" @click="point.sources.splice(j, 1)">移除依据</button></div>
        <button type="button" class="btn" :disabled="point.sources.length >= 6" @click="picking = point">关联原文</button><small v-if="!point.sources.length"> 尚未关联原文</small>
        <details><summary>关联已核对的原文件标准（可选）</summary><p class="small muted">用于判断内容是否从解析开始就丢失。先在“文档解析验收”保存原文件标准。</p><select v-model="point.original_standard_id" @change="point.original_check_id = ''"><option value="">未关联原文件标准</option><option v-for="s in originalStandards.filter(s => point.sources.some(source => source.document_id === s.payload.document_id))" :key="s.id" :value="s.id">{{ s.payload.key }}</option></select><select v-if="point.original_standard_id" v-model="point.original_check_id"><option value="">选择页面标准项</option><option v-for="c in checksFor(point)" :key="c.id" :value="c.id">{{ c.label }}</option></select></details>
      </div><button type="button" class="btn" :disabled="value.criteria.length >= 30" @click="addPoint">添加验收要点</button>
    </section>
    <details><summary>可选检查：清单数量、编号与重复</summary><label>预期条目数（留空不检查）<input v-model.number="value.list_checks.expected_count" type="number" min="1" max="100" aria-label="预期条目数"></label><label class="acceptance-check"><input v-model="value.list_checks.sequential" type="checkbox">编号从 1 开始且连续</label><label class="acceptance-check"><input v-model="value.list_checks.no_duplicates" type="checkbox">不允许相同内容重复凑数</label><p class="small muted">达到数量不代表答全；每个要点仍需逐项核对。</p></details>
    <label class="acceptance-check"><input v-model="value.check_citation_structure" type="checkbox">检查正文引用编号及每行表格引用</label><p class="small muted">程序检查引用是否存在及编号有效；事实与原文是否对应仍需语义评审。</p>
    <label class="acceptance-check"><input v-model="value.check_relevance" type="checkbox">检查回答相关性与重复附加内容</label><p class="small muted">独立检查整段回答是否偏题；必要条件、例外、单位和范围说明仍须保留。结果支持人工复核。</p>
    <label>依据说明<textarea v-model="value.source_notes" rows="2" placeholder="资料不足或冲突案例，请说明检查范围及预期行为的依据。"/></label>
    <details><summary>案例编号、范围、必要引用与多轮问题</summary><div class="acceptance-fields"><label>案例编号<input v-model="value.key" :disabled="!!editor.id" maxlength="80" placeholder="留空自动生成"></label><label>分类<input v-model="value.category" maxlength="100"></label></div>
      <fieldset><legend>本题可读取的文件（不选表示当前知识库）</legend><label v-for="d in documents" :key="d.document_id" class="acceptance-check"><input v-model="value.reading.document_ids" type="checkbox" :value="d.document_id">{{ d.filename }}</label></fieldset>
      <fieldset><legend>答案必须引用的文件</legend><label v-for="d in documents" :key="d.document_id" class="acceptance-check"><input v-model="value.required_source_documents" type="checkbox" :value="d.document_id">{{ d.filename }}</label></fieldset>
      <fieldset><legend>检索必须包含的文件</legend><label v-for="d in documents" :key="d.document_id" class="acceptance-check"><input v-model="value.required_retrieved_documents" type="checkbox" :value="d.document_id">{{ d.filename }}</label></fieldset>
      <div class="acceptance-fields"><label>阅读方式<select v-model="value.reading.mode"><option value="auto">自动判断</option><option value="fact">查找具体问题</option><option value="overview">逐章总结</option><option value="compare">跨文件综合</option></select></label><label>至少引用几份文件<input v-model.number="value.minimum_distinct_cited_documents" type="number" min="0" max="20"></label></div>
      <label>历史前置问题 JSON<textarea v-model="history" rows="4"/></label>
      <button v-if="editor.id" type="button" class="btn" @click="loadRevisions">查看历史版本</button><details v-for="r in revisions" :key="r.revision"><summary>v{{ r.revision }}</summary><pre>{{ JSON.stringify(r.payload, null, 2) }}</pre></details>
    </details>
    <label class="acceptance-check"><input :checked="value.review_state === 'confirmed'" type="checkbox" @change="value.review_state = $event.target.checked ? 'confirmed' : 'candidate'">我已核对原文和预期要求，确认该案例可以运行</label>
    <ErrorNotice v-if="localError || error" :error="localError || error"/><footer><button class="btn" type="button" @click="emit('close')">取消</button><button class="btn primary" :disabled="busy" type="submit">保存案例</button></footer>
  </form>
</AppDialog><AcceptanceSourcePicker v-if="picking" :space="space" :documents="documents" @close="picking = null" @select="attach"/></template>
