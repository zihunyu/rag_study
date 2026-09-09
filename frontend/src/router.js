import { createRouter, createWebHistory } from 'vue-router';
import { useAuth } from './stores/auth.js';
export const router = createRouter({ history: createWebHistory(), routes: [
  { path: '/', redirect: '/knowledge-bases' },
  { path: '/login', component: () => import('./pages/LoginPage.vue'), meta: { public: true } },
  { path: '/change-password', component: () => import('./pages/PasswordPage.vue'), meta: { account: true } },
  { path: '/available-knowledge-bases', component: () => import('./pages/AvailableLibrariesPage.vue'), meta: { section: '可问答知识库' } },
  { path: '/admin/users', component: () => import('./pages/UsersPage.vue'), meta: { section: '用户管理', admin: true } },
  { path: '/admin/audit', component: () => import('./pages/AuditPage.vue'), meta: { section: '审计记录', admin: true } },
  { path: '/knowledge-bases', component: () => import('./pages/LibrariesPage.vue'), meta: { section: '知识库', manage: true } },
  { path: '/knowledge-bases/:spaceId/documents/:documentId', component: () => import('./pages/DocumentPage.vue'), meta: { section: '知识库', detail: true } },
  { path: '/knowledge-bases/:spaceId/:view(documents|retrieval|settings|members|activity)?', component: () => import('./pages/LibraryPage.vue'), meta: { section: '知识库' } },
  { path: '/chat/:conversationId?', component: () => import('./pages/ChatPage.vue'), meta: { section: '知识问答' } },
  { path: '/tasks', component: () => import('./pages/TasksPage.vue'), meta: { section: '任务中心', manage: true } },
  { path: '/acceptance/:spaceId?', component: () => import('./pages/AcceptancePage.vue'), meta: { section: '问答验收', manage: true } },
  { path: '/system', component: () => import('./pages/SystemPage.vue'), meta: { section: '系统状态', admin: true } },
  { path: '/:pathMatch(.*)*', redirect: '/knowledge-bases' },
], scrollBehavior: () => ({ top: 0 }) });
router.beforeEach(async to => {
  const auth = useAuth();
  try { await auth.init(); } catch { if (to.path !== '/login') return '/login'; }
  if (to.meta.public) return auth.user ? auth.home() : true;
  if (!auth.user) return '/login';
  if (auth.user.must_change_password && to.path !== '/change-password') return '/change-password';
  if (to.meta.admin && !auth.isSuper || to.meta.manage && !auth.manages) return auth.home();
  if (to.params.spaceId && !auth.canManage(to.params.spaceId)) return '/chat';
});
window.addEventListener('ragkb-session-ended', event => { if (!['login', 'permissions'].includes(event.detail)) router.replace(`/login?reason=${encodeURIComponent(event.detail || 'expired')}`); });
