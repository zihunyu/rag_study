<script setup>
import { computed } from 'vue';
import MarkdownIt from 'markdown-it';
import DOMPurify from 'dompurify';
const props = defineProps({ text: { type: String, default: '' } });
const md = new MarkdownIt({ html: false, linkify: false, breaks: true });
const html = computed(() => DOMPurify.sanitize(md.render(props.text), { FORBID_TAGS: ['img', 'video', 'audio', 'iframe', 'style', 'input'], FORBID_ATTR: ['style'] }));
</script>
<template><div class="markdown-content" v-html="html"/></template>
