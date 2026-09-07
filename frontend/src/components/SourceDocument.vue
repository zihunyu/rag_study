<script setup>
import { ref, watch, nextTick, onUnmounted } from 'vue';
import DOMPurify from 'dompurify';
import { apiUrl } from '../api.js';
const props = defineProps({ versionId: String, filename: String });
const host = ref(null), loading = ref(false), error = ref(''), pdf = ref('');
let controller, revision = 0;
watch(() => [props.versionId, props.filename], async () => {
  const own = ++revision; controller?.abort(); controller = new AbortController();
  if (host.value) host.value.replaceChildren();
  if (pdf.value) URL.revokeObjectURL(pdf.value); pdf.value = ''; error.value = '';
  if (!props.versionId) return;
  const extension = props.filename?.split('.').pop()?.toLowerCase();
  if (!['pdf','docx'].includes(extension)) { error.value = '此格式请下载原文件阅读，图片可在下方逐张对照。'; return; }
  loading.value = true;
  try {
    const response = await fetch(apiUrl(`/document-versions/${props.versionId}/original/preview`), { signal: controller.signal });
    if (!response.ok) throw new Error();
    const buffer = await response.arrayBuffer();
    if (own !== revision) return;
    if (extension === 'pdf') pdf.value = URL.createObjectURL(new Blob([buffer], { type: 'application/pdf' }));
    else { const { renderAsync } = await import('docx-preview'); await nextTick(); if (own !== revision || !host.value) return;
      const sandbox = document.createElement('div'); await renderAsync(buffer, sandbox, undefined, { inWrapper: true, ignoreWidth: false, useBase64URL: true });
      if (own === revision && host.value) host.value.innerHTML = DOMPurify.sanitize(sandbox.innerHTML, { ADD_TAGS: ['style'], FORBID_TAGS: ['script','iframe','object','embed','form','input'] });
    }
  } catch (cause) { if (own === revision && cause.name !== 'AbortError') error.value = '原文档预览失败，请下载原文件查看。'; }
  finally { if (own === revision) loading.value = false; }
}, { immediate: true });
onUnmounted(() => { ++revision; controller?.abort(); if (pdf.value) URL.revokeObjectURL(pdf.value); });
</script>
<template><div class="source-document"><p v-if="loading" class="muted">正在加载原文档…</p><p v-if="error" class="muted">{{ error }}</p><iframe v-if="pdf" :src="pdf" title="原文档 PDF"/><div ref="host" class="source-docx"/></div></template>
