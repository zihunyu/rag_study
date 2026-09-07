<script setup>
import { computed } from 'vue';
import MarkdownIt from 'markdown-it';
import DOMPurify from 'dompurify';
const props = defineProps({ text: { type: String, default: '' }, sourceTables: Boolean });
const md = new MarkdownIt({ html: false, linkify: false, breaks: true });
md.block.ruler.before('paragraph', 'source_table', (state, startLine, endLine, silent) => {
  if (!props.sourceTables) return false;
  const start = state.bMarks[startLine] + state.tShift[startLine];
  if (!/^<table[\s>]/i.test(state.src.slice(start))) return false;
  for (let line = startLine; line < endLine; line++) {
    const content = state.src.slice(start, state.eMarks[line]);
    if (!/<\/table>\s*$/i.test(content)) continue;
    if (silent) return true;
    const token = state.push('source_table', 'table', 0);
    token.content = content;
    state.line = line + 1;
    return true;
  }
  return false;
});
md.renderer.rules.source_table = (tokens, index) => DOMPurify.sanitize(tokens[index].content, {
  ALLOWED_TAGS: ['table', 'thead', 'tbody', 'tfoot', 'tr', 'td', 'th', 'caption'],
  ALLOWED_ATTR: ['rowspan', 'colspan'],
});
const html = computed(() => DOMPurify.sanitize(md.render(props.text), { FORBID_TAGS: ['img', 'video', 'audio', 'iframe', 'style', 'input'], FORBID_ATTR: ['style'] }));
</script>
<template><div class="markdown-content" v-html="html"/></template>
