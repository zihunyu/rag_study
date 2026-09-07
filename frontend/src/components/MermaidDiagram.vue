<script setup>
import { ref, watch, onUnmounted } from 'vue';
import DOMPurify from 'dompurify';
const props = defineProps({ code: { type: String, required: true } });
const svg = ref(''), error = ref(''), loading = ref(false), sourceOpen = ref(false);
let revision = 0;
watch(() => props.code, async code => {
  const own = ++revision; svg.value = ''; error.value = ''; loading.value = true;
  try {
    if (!/^flowchart (TB|LR|BT|RL)\s/.test(code) || /%%\{|^\s*click\s/im.test(code)) throw new Error('图形包含不支持的指令');
    const { renderDiagram } = await import('../mermaid.js');
    const result = await renderDiagram(code);
    if (own !== revision) return;
    svg.value = DOMPurify.sanitize(result, { USE_PROFILES: { svg: true, svgFilters: true }, FORBID_TAGS: ['foreignObject', 'script'], FORBID_ATTR: ['onload', 'onclick'] });
  } catch { if (own === revision) error.value = '重绘图暂时无法显示，请查看原图或展开结构代码。'; }
  finally { if (own === revision) loading.value = false; }
}, { immediate: true });
onUnmounted(() => { ++revision; });
function download() {
  const url = URL.createObjectURL(new Blob([svg.value], { type: 'image/svg+xml' }));
  const link = document.createElement('a'); link.href = url; link.download = 'diagram.svg'; link.click(); URL.revokeObjectURL(url);
}
</script>
<template><div class="mermaid-diagram"><p v-if="loading" class="muted">正在绘制结构图…</p><p v-if="error" class="notice warning">{{ error }}</p><div v-if="svg" class="diagram-svg" v-html="svg"/><div class="visual-actions"><span class="small muted">根据原图重绘，布局由程序排列</span><button v-if="svg" class="btn small-button" @click="download">导出 SVG</button></div><details @toggle="sourceOpen = $event.target.open"><summary class="small muted">结构代码</summary><pre v-if="sourceOpen">{{ code }}</pre></details></div></template>
