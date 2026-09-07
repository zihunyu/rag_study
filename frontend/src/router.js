import { createRouter, createWebHistory } from 'vue-router';
export const router = createRouter({ history: createWebHistory(), routes: [
  { path: '/', redirect: '/knowledge-bases' },
  { path: '/knowledge-bases', component: () => import('./pages/LibrariesPage.vue'), meta: { section: '知识库' } },
  { path: '/knowledge-bases/:spaceId/documents/:documentId', component: () => import('./pages/DocumentPage.vue'), meta: { section: '知识库', detail: true } },
  { path: '/knowledge-bases/:spaceId/:view(documents|retrieval|settings)?', component: () => import('./pages/LibraryPage.vue'), meta: { section: '知识库' } },
  { path: '/chat/:conversationId?', component: () => import('./pages/ChatPage.vue'), meta: { section: '知识问答' } },
  { path: '/tasks', component: () => import('./pages/TasksPage.vue'), meta: { section: '任务中心' } },
  { path: '/system', component: () => import('./pages/SystemPage.vue'), meta: { section: '系统状态' } },
  { path: '/:pathMatch(.*)*', redirect: '/knowledge-bases' },
], scrollBehavior: () => ({ top: 0 }) });
