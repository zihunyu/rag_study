<script setup>
import { computed, onMounted, ref, watch } from 'vue';
import { useRoute } from 'vue-router';
import { Library, MessagesSquare, ListTodo, Activity, Layers, Menu, ChevronRight, PanelLeftClose, ArrowUpRight } from '@lucide/vue';
import { useWorkspace } from './stores/workspace.js';
import { useUploads } from './stores/uploads.js';
const route = useRoute(), workspace = useWorkspace(), uploads = useUploads();
const sidebarOpen = ref(false);
const navigation = [{ path: '/knowledge-bases', name: '知识库', icon: Library }, { path: '/chat', name: '知识问答', icon: MessagesSquare }, { path: '/tasks', name: '任务中心', icon: ListTodo }, { path: '/system', name: '系统状态', icon: Activity }];
const currentSpace = computed(() => workspace.spaces.find(s => s.id === route.params.spaceId));
watch(() => route.fullPath, () => { sidebarOpen.value = false; if (route.params.spaceId) workspace.select(route.params.spaceId); });
onMounted(async () => { await workspace.init(); await uploads.restore(); });
</script>
<template><div class="app-shell">
  <button v-if="sidebarOpen" class="sidebar-backdrop" aria-label="关闭导航" @click="sidebarOpen = false"/>
  <aside class="sidebar" :class="{ open: sidebarOpen }">
    <RouterLink to="/knowledge-bases" class="brand"><span class="brand-symbol"><Layers :size="22"/></span><div><strong>RAG<span>SPACE</span></strong><small>知识管理工作台</small></div></RouterLink>
    <div class="workspace-label"><span class="workspace-dot"/>本地工作空间<span class="mini-label">LOCAL</span></div>
    <p class="nav-caption">工作空间</p><nav aria-label="主导航"><RouterLink v-for="item in navigation" :key="item.path" :to="item.path" :class="['nav-item', { selected: route.meta.section === item.name }]"><component :is="item.icon" :size="19"/><span>{{ item.name }}</span><span v-if="item.path === '/tasks' && uploads.activeCount" class="nav-count">{{ uploads.activeCount }}</span></RouterLink></nav>
    <div class="sidebar-libraries"><p class="nav-caption">最近的知识库</p><RouterLink v-for="space in workspace.spaces.slice(0, 5)" :key="space.id" :to="`/knowledge-bases/${space.id}/documents`" class="recent-library"><span class="small-square"/>{{ space.name }}</RouterLink><p v-if="!workspace.spaces.length" class="small muted">创建一个知识库开始使用</p></div>
    <div class="sidebar-footer"><div class="footer-mark"><Layers :size="16"/><span>让知识成为可靠答案</span></div><RouterLink to="/system">运行状态<ArrowUpRight :size="14"/></RouterLink></div>
  </aside>
  <div class="main-shell"><header class="topbar"><div class="breadcrumbs"><button class="icon-button mobile-menu" aria-label="展开导航" @click="sidebarOpen = !sidebarOpen"><Menu :size="20"/></button><PanelLeftClose :size="17" class="desktop-only muted"/><span class="breadcrumb-root">工作空间</span><ChevronRight :size="14"/><RouterLink :to="navigation.find(n => n.name === route.meta.section)?.path || '/'">{{ route.meta.section }}</RouterLink><template v-if="currentSpace"><ChevronRight :size="14"/><span class="truncate">{{ currentSpace.name }}</span></template></div><span class="local-chip">本地单用户</span></header><main id="main-content"><RouterView/></main></div>
</div></template>
