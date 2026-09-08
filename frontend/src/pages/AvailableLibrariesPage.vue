<script setup>
import { onMounted } from 'vue';
import { BookOpen, ArrowUpRight } from '@lucide/vue';
import { useWorkspace } from '../stores/workspace.js';
import EmptyState from '../components/EmptyState.vue';
import ErrorNotice from '../components/ErrorNotice.vue';
const workspace = useWorkspace(); onMounted(() => workspace.refresh());
</script>
<template><div class="page"><div class="page-heading"><div><span class="eyebrow">YOUR KNOWLEDGE</span><h1>可问答知识库</h1><p class="page-description">选择一个知识库，让回答聚焦于这份资料。</p></div></div><ErrorNotice :error="workspace.error" retry @retry="workspace.refresh"/><div v-if="workspace.loading" class="skeleton-card"/><div v-else-if="workspace.spaces.length" class="library-grid"><RouterLink v-for="space in workspace.spaces" :key="space.id" :to="`/chat?space=${space.id}`" class="panel library-card"><span class="library-symbol"><BookOpen :size="25"/></span><h2>{{ space.name }}</h2><p class="muted">{{ space.description || '已分配给你的知识库' }}</p><span class="text-link">开始问答<ArrowUpRight :size="15"/></span></RouterLink></div><EmptyState v-else title="尚未分配知识库" description="请联系管理员分配知识库后，再开始问答。"/></div></template>
