<script setup>
import { nextTick, onUnmounted, ref, watch } from 'vue';
import { sessionFetch, authEpoch } from '../authTransport.js';
import { apiUrl } from '../api.js';
import ErrorNotice from './ErrorNotice.vue';
const props = defineProps({ versionId: String, mime: String, page: { type: Number, default: 1 } });
const url = ref(''), text = ref(''), error = ref(null), busy = ref(false), word = ref(null), type = ref('');
let revision = 0;
function clear() { if (url.value) URL.revokeObjectURL(url.value); url.value = ''; text.value = ''; if (word.value) word.value.replaceChildren(); }
watch(() => [props.versionId, props.mime], async () => {
  const own = ++revision, account = authEpoch(); clear(); error.value = null;
  if (!props.versionId) return;
  busy.value = true;
  try {
    const response = await sessionFetch(apiUrl(`/document-versions/${props.versionId}/original/preview`));
    if (!response.ok) throw new Error('无法读取原文件，请检查当前版本及权限');
    const data = await response.arrayBuffer();
    if (own !== revision || account !== authEpoch()) return;
    type.value = props.mime || 'application/octet-stream';
    const blob = new Blob([data], { type: type.value });
    url.value = URL.createObjectURL(blob);
    if (type.value.startsWith('text/')) text.value = await blob.text();
    if (type.value.includes('wordprocessingml')) {
      const { renderAsync } = await import('docx-preview');
      await nextTick();
      if (own === revision && word.value) await renderAsync(data, word.value, undefined, { ignoreWidth: true, breakPages: true });
    }
  } catch (cause) { if (own === revision) error.value = cause; }
  finally { if (own === revision) busy.value = false; }
}, { immediate: true });
onUnmounted(() => { ++revision; clear(); });
</script>
<template><section class="original-preview"><h3>原文件</h3><p v-if="busy">正在读取原文件…</p><ErrorNotice :error="error"/>
  <iframe v-if="url && type === 'application/pdf'" :src="`${url}#page=${page}`" title="原文件页面"/>
  <img v-else-if="url && type.startsWith('image/') && type !== 'image/svg+xml'" :src="url" alt="原文件图像">
  <pre v-else-if="text">{{ text }}</pre><div v-show="type.includes('wordprocessingml')" ref="word"/>
  <p v-if="url && type !== 'application/pdf' && !type.startsWith('image/') && !type.startsWith('text/') && !type.includes('wordprocessingml')">此格式请下载后在原应用中核对，解析结果在右侧展示。</p>
  <a v-if="url" :href="url" download="原文件">下载原文件</a>
</section></template>
