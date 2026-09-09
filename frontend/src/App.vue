<script setup>
import { computed, onMounted, onBeforeUnmount, ref, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { Library, MessagesSquare, ListTodo, Activity, Layers, Menu, ChevronRight, PanelLeftClose, ArrowUpRight, Users, ShieldCheck, LogOut, KeyRound } from '@lucide/vue';
import { useAuth } from './stores/auth.js';
import { useWorkspace } from './stores/workspace.js';
import { useUploads } from './stores/uploads.js';
const auth = useAuth(), router = useRouter();
const route = useRoute(), workspace = useWorkspace(), uploads = useUploads();
const sidebarOpen = ref(false), accountOpen = ref(false);
const navigation = computed(() => [{ path: '/knowledge-bases', name: '知识库', icon: Library }, { path: '/chat', name: '知识问答', icon: MessagesSquare }, { path: '/tasks', name: '任务中心', icon: ListTodo }, { path: '/acceptance', name: '问答验收', icon: ShieldCheck }, { path: '/admin/users', name: '用户管理', icon: Users, admin: true }, { path: '/admin/audit', name: '审计记录', icon: ShieldCheck, admin: true }, { path: '/system', name: '系统状态', icon: Activity, admin: true }, { path: '/available-knowledge-bases', name: '可问答知识库', icon: Library, reader: true }].filter(n => n.admin ? auth.isSuper : n.reader ? !auth.manages : ['/knowledge-bases', '/tasks', '/acceptance'].includes(n.path) ? auth.manages : true));
const currentSpace = computed(() => workspace.spaces.find(s => s.id === route.params.spaceId));
watch(() => route.fullPath, () => { sidebarOpen.value = false; if (route.params.spaceId) workspace.select(route.params.spaceId); });
async function loadWorkspace() {
  if (!auth.user || auth.user.must_change_password) return;
  await workspace.init(); if (auth.manages) await uploads.restore();
}
watch(() => auth.user?.id, loadWorkspace);
async function refreshIdentity() { if (auth.user && document.visibilityState === 'visible') { try { await auth.refresh(); } catch { /* retry on focus */ } } }
async function permissionsChanged() { await workspace.refresh(); if ((route.meta.admin && !auth.isSuper) || (route.meta.manage && !auth.manages) || (route.params.spaceId && !auth.canManage(route.params.spaceId))) router.replace(auth.home()); }
let timer;
onMounted(async () => { try { await auth.init(); await loadWorkspace(); } catch { /* login page has retry */ }
  window.addEventListener('focus', refreshIdentity); window.addEventListener('ragkb-permissions-changed', permissionsChanged);
  timer = setInterval(refreshIdentity, 30000);
});
onBeforeUnmount(() => { clearInterval(timer); window.removeEventListener('focus', refreshIdentity); window.removeEventListener('ragkb-permissions-changed', permissionsChanged); });
</script>
<template><RouterView v-if="route.meta.public || route.meta.account"/><div v-else-if="!auth.user" class="auth-loading">正在检查登录状态…</div><div v-else class="app-shell">
  <button v-if="sidebarOpen" class="sidebar-backdrop" aria-label="关闭导航" @click="sidebarOpen = false"/>
  <aside class="sidebar" :class="{ open: sidebarOpen }">
    <RouterLink :to="auth.home()" class="brand"><span class="brand-symbol"><Layers :size="22"/></span><div><strong>RAG<span>SPACE</span></strong><small>知识管理工作台</small></div></RouterLink>
    <div class="workspace-label"><span class="workspace-dot"/>团队知识空间<span class="mini-label">WORKSPACE</span></div>
    <p class="nav-caption">工作空间</p><nav aria-label="主导航"><RouterLink v-for="item in navigation" :key="item.path" :to="item.path" :class="['nav-item', { selected: route.meta.section === item.name }]"><component :is="item.icon" :size="19"/><span>{{ item.name }}</span><span v-if="item.path === '/tasks' && uploads.activeCount" class="nav-count">{{ uploads.activeCount }}</span></RouterLink></nav>
    <div class="sidebar-libraries"><p class="nav-caption">最近的知识库</p><RouterLink v-for="space in workspace.spaces.slice(0, 5)" :key="space.id" :to="auth.canManage(space.id) ? `/knowledge-bases/${space.id}/documents` : `/chat?space=${space.id}`" class="recent-library"><span class="small-square"/>{{ space.name }}</RouterLink><p v-if="!workspace.spaces.length" class="small muted">{{ auth.isSuper ? '创建一个知识库开始使用' : '尚未分配知识库' }}</p></div>
    <div class="sidebar-footer"><div class="footer-mark"><Layers :size="16"/><span>让知识成为可靠答案</span></div><RouterLink v-if="auth.isSuper" to="/system">运行状态<ArrowUpRight :size="14"/></RouterLink></div>
  </aside>
  <div class="main-shell"><header class="topbar"><div class="breadcrumbs"><button class="icon-button mobile-menu" aria-label="展开导航" @click="sidebarOpen = !sidebarOpen"><Menu :size="20"/></button><PanelLeftClose :size="17" class="desktop-only muted"/><span class="breadcrumb-root">工作空间</span><ChevronRight :size="14"/><RouterLink :to="navigation.find(n => n.name === route.meta.section)?.path || '/'">{{ route.meta.section }}</RouterLink><template v-if="currentSpace"><ChevronRight :size="14"/><span class="truncate">{{ currentSpace.name }}</span></template></div><div class="account-menu"><button class="btn ghost account-trigger" :aria-expanded="accountOpen" @click="accountOpen = !accountOpen"><span class="account-avatar">{{ auth.user.display_name?.slice(0, 1) }}</span><span class="account-name">{{ auth.user.display_name }}</span><span class="role-chip">{{ auth.roleLabel }}</span></button><div v-if="accountOpen" class="account-dropdown"><p>{{ auth.user.display_name }}<small class="account-row-details">{{ auth.roleLabel }}</small></p><button v-if="auth.mode === 'password'" class="btn ghost" @click="accountOpen = false; router.push('/change-password')"><KeyRound :size="16"/>修改密码</button><button v-if="auth.mode === 'password'" class="btn ghost" @click="accountOpen = false; auth.logout()"><LogOut :size="16"/>退出登录</button><p v-else class="small muted">本机初始化模式</p></div></div></header><main id="main-content"><RouterView :key="`${auth.user.id}:${auth.user.auth_revision || 0}`"/></main></div>
</div></template>
