<script setup>
import { ref } from 'vue';
import { request } from '../api.js';
import AppDialog from './AppDialog.vue';
import ErrorNotice from './ErrorNotice.vue';
const props = defineProps({ space: String, documents: Array });
const emit = defineEmits(['select', 'close']);
const documentId = ref(''), rows = ref([]), next = ref(null), error = ref(null), busy = ref(false);
let revision = 0;
async function load(more = false) {
  const own = ++revision, id = documentId.value;
  if (!more) { rows.value = []; next.value = null; }
  if (!id) return;
  busy.value = true; error.value = null;
  try {
    const data = await request(`/spaces/${props.space}/acceptance/sources/${id}?offset=${more ? next.value : 0}`);
    if (own !== revision) return;
    rows.value.push(...data.items); next.value = data.next_offset;
  } catch (cause) { if (own === revision) error.value = cause; }
  finally { if (own === revision) busy.value = false; }
}
const position = row => [row.locator?.section_path || row.locator?.heading, row.locator?.page ? `第 ${row.locator.page} 页` : ''].filter(Boolean).join(' · ');
</script>
<template><AppDialog :open="true" title="选择原文依据" class="acceptance-dialog" @close="emit('close')">
  <p>选择已发布资料中的原文。保存时再次检查版本、权限和摘录是否真实存在。</p>
  <label>文件<select v-model="documentId" aria-label="依据文件" @change="load(false)"><option value="">选择文件</option><option v-for="d in documents" :key="d.document_id" :value="d.document_id">{{ d.filename }}</option></select></label>
  <ErrorNotice v-if="error" :error="error"/><p v-if="busy">正在读取原文…</p>
  <section v-for="row in rows" :key="row.chunk_id" class="source-choice"><p>{{ position(row) }}</p><pre>{{ row.text }}</pre><button class="btn" :disabled="!row.text || row.text.length > 4000" @click="emit('select', { document_id: row.document_id, version_id: row.version_id, chunk_id: row.chunk_id, quote: row.text })">使用此段原文</button><small v-if="row.text.length > 4000">此段过长，请选择更具体的段落。</small></section>
  <button v-if="next !== null" class="btn" :disabled="busy" @click="load(true)">更多原文</button>
</AppDialog></template>
