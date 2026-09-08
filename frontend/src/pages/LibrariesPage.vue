<script setup>
import { useAuth } from '../stores/auth.js';
import LibraryRecycleBin from '../components/LibraryRecycleBin.vue';
const auth = useAuth();
import { computed, ref } from 'vue';
import { useRouter } from 'vue-router';
import { Plus, Search, LayoutGrid, List, BookOpen, ArrowUpRight, FileText, CircleCheck, Layers, Clock3 } from '@lucide/vue';
import { useWorkspace } from '../stores/workspace.js';
import { command, request } from '../api.js';
import { dateTime } from '../format.js';
import AppDialog from '../components/AppDialog.vue';
import EmptyState from '../components/EmptyState.vue';
import ErrorNotice from '../components/ErrorNotice.vue';
const store = useWorkspace(), router = useRouter();
const search = ref(''), layout = ref('grid'), open = ref(false), name = ref(''), description = ref(''), busy = ref(false), error = ref(null);
const filtered = computed(() => store.spaces.filter(space => `${space.name} ${space.description}`.toLowerCase().includes(search.value.toLowerCase())));
const metrics = computed(() => [{ label: '知识库', value: store.spaces.length, icon: BookOpen, hint: '统一组织你的知识' }, { label: '文档总数', value: store.totals.document_count ?? 0, icon: FileText, hint: '所有已提交的文档' }, { label: '可用于问答', value: store.totals.answerable_count ?? 0, icon: CircleCheck, hint: '状态与索引一致的文档' }, { label: '待处理', value: store.totals.pending_count ?? 0, icon: Clock3, hint: '待复核、失败或异常' }]);
async function create() {
  if (!name.value.trim() || busy.value) return;
  busy.value = true; error.value = null;
  try {
    const space = await command('/spaces', { name: name.value.trim() });
    if (description.value.trim()) await request(`/spaces/${space.id}`, { method: 'PATCH', body: JSON.stringify({ name: name.value.trim(), description: description.value.trim() }) });
    await store.refresh(); store.select(space.id); open.value = false; router.push(`/knowledge-bases/${space.id}/documents`);
  } catch (cause) { error.value = cause; } finally { busy.value = false; }
}
</script>
<template><div class="page libraries-page">
  <div class="page-heading"><div><p class="eyebrow">KNOWLEDGE WORKSPACE</p><h1>你的知识，从这里连接。</h1><p class="page-description">组织文档、沉淀知识，让每个答案都有据可循。</p></div><button v-if="auth.isSuper" class="btn primary" @click="open = true"><Plus :size="18"/>创建知识库</button></div>
  <ErrorNotice :error="store.error" retry @retry="store.refresh"/>
  <div class="metrics-grid"><div v-for="(metric, i) in metrics" :key="metric.label" class="metric-card"><div class="metric-top"><span>{{ metric.label }}</span><component :is="metric.icon" :size="17"/></div><div :class="['metric-number', { accent: i === 2 }]">{{ store.loading && !store.spaces.length ? '—' : metric.value.toLocaleString() }}<span v-if="i === 2" class="metric-mark">READY</span></div><small>{{ metric.hint }}</small></div></div>
  <div class="section-toolbar"><div class="section-title"><h2>{{ auth.isSuper ? '全部知识库' : '已分配知识库' }}</h2><span class="count-pill">{{ filtered.length }}</span></div><div class="toolbar-actions"><LibraryRecycleBin v-if="auth.mode === 'password'"/><label class="search-field"><Search :size="17"/><input v-model="search" aria-label="搜索知识库" placeholder="搜索知识库…"/></label><div class="segmented"><button :class="{ active: layout === 'grid' }" aria-label="卡片视图" :aria-pressed="layout === 'grid'" @click="layout = 'grid'"><LayoutGrid :size="17"/></button><button :class="{ active: layout === 'list' }" aria-label="列表视图" :aria-pressed="layout === 'list'" @click="layout = 'list'"><List :size="18"/></button></div></div></div>
  <div v-if="store.loading && !store.spaces.length" class="library-grid"><div v-for="i in 3" :key="i" class="skeleton-card"/></div>
  <div v-else-if="filtered.length" :class="layout === 'grid' ? 'library-grid' : 'library-list'"><RouterLink v-for="(space, i) in filtered" :key="space.id" :to="auth.canManage(space.id) ? `/knowledge-bases/${space.id}/documents` : `/chat?space=${space.id}`" class="library-card"><div class="library-card-top"><span class="library-symbol" :data-color="i % 3"><BookOpen :size="24"/></span><span class="library-status"><i/>{{ space.processing_count ? '处理中' : '知识库' }}</span><ArrowUpRight :size="18" class="card-arrow"/></div><div class="library-card-body"><h3>{{ space.name }}</h3><p>{{ space.description || '添加文档，为这个知识库建立可靠的知识来源。' }}</p></div><div v-if="auth.canManage(space.id)" class="library-card-stats"><span><FileText :size="14"/>{{ space.document_count }} 份文档</span><span><Layers :size="14"/>{{ space.chunk_count }} 个分块</span></div><div class="library-card-footer"><span>{{ space.answerable_count }} 份可问答<span v-if="space.pending_count" class="pending-note"> · {{ space.pending_count }} 份待处理</span></span><small>{{ dateTime(space.updated_ms / 1000) }}</small></div></RouterLink><button v-if="auth.isSuper && layout === 'grid' && !search" class="create-library-card" @click="open = true"><span><Plus :size="25"/></span><strong>建立新的知识连接</strong><small>创建知识库，开始整理你的资料</small></button></div>
  <EmptyState v-else :title="search ? '没有匹配的知识库' : auth.isSuper ? '建立你的第一个知识库' : '尚未分配可用知识库'" :description="search ? '试试其他名称或关键词。' : auth.isSuper ? '上传你的文档，整理成可检索、可追溯的知识。' : '联系总管理员分配知识库，或查看回收站。'"><button v-if="auth.isSuper && !search" class="btn primary" @click="open = true"><Plus :size="17"/>创建知识库</button></EmptyState>
  <div class="workspace-note"><Layers :size="18"/><div><strong>从资料到知识，保持每一步可见</strong><span>上传与解析 → 检查质量 → 确认发布 → 可信问答</span></div></div>
  <AppDialog :open="open" title="创建知识库" @close="open = false"><form @submit.prevent="create"><label class="field-label" for="space-name">知识库名称 <span class="accent">*</span></label><input id="space-name" v-model="name" autofocus maxlength="100" required placeholder="例如：产品手册、团队制度"/><label class="field-label" for="space-description">描述 <span class="muted">（可选）</span></label><textarea id="space-description" v-model="description" maxlength="2000" rows="3" placeholder="这个知识库用来存放哪些资料？"/><ErrorNotice :error="error"/><div class="dialog-actions"><button type="button" class="btn" @click="open = false">取消</button><button class="btn primary" :disabled="busy || !name.trim()">{{ busy ? '创建中…' : '创建知识库' }}</button></div></form></AppDialog>
</div></template>
