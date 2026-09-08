import { defineStore } from 'pinia';
import { computed, ref } from 'vue';
import { request } from '../api.js';
import { configureSession, csrfToken, onSessionReset, resetSession } from '../authTransport.js';

export const useAuth = defineStore('auth', () => {
  const user = ref(null), ready = ref(false), mode = ref(''), error = ref(null);
  const isSuper = computed(() => user.value?.global_role === 'super_admin');
  const manages = computed(() => !!user.value?.capabilities?.includes('manage'));
  const roleLabel = computed(() => isSuper.value ? '总管理员' : manages.value ? '知识库管理员' : '普通用户');
  let initializing = null;
  const channel = typeof window !== 'undefined' && typeof window.BroadcastChannel === 'function' ? new window.BroadcastChannel('ragkb-account') : null;
  onSessionReset(reason => { user.value = null; ready.value = true; error.value = null;
    if (typeof window !== 'undefined') window.dispatchEvent(new CustomEvent('ragkb-session-ended', { detail: reason }));
  });
  if (channel) channel.onmessage = () => resetSession('changed');
  function canManage(spaceId) { return isSuper.value || user.value?.spaces?.some(s => s.id === spaceId && s.my_role === 'manager' && !s.deleted); }
  function home() {
    if (user.value?.must_change_password) return '/change-password';
    if (isSuper.value) return '/knowledge-bases';
    const first = user.value?.spaces?.find(s => s.my_role === 'manager' && !s.deleted);
    return first ? `/knowledge-bases/${first.id}/documents` : '/chat';
  }
  async function refresh(silent = false) {
    try { const next = await request('/auth/me', { silentUnauthorized: silent }); const previous = user.value;
      if (previous && previous.id !== next.id) { resetSession('changed'); const config = await request('/auth/csrf'); configureSession(config.auth_mode, config.csrf_token); }
      else if (previous && previous.auth_revision !== next.auth_revision) {
        const token = csrfToken(); resetSession('permissions'); configureSession(mode.value, token);
      }
      user.value = next; error.value = null;
      if (previous && previous.auth_revision !== next.auth_revision) window.dispatchEvent(new Event('ragkb-permissions-changed'));
      return next;
    } catch (cause) { if (cause.status === 401) user.value = null; else { error.value = cause; throw cause; } }
    finally { ready.value = true; }
  }
  async function init(force = false) {
    if (initializing) return initializing;
    if (ready.value && !force) return;
    initializing = (async () => { try {
      const config = await request('/auth/csrf'); mode.value = config.auth_mode;
      configureSession(config.auth_mode, config.csrf_token);
      await refresh(true);
    } finally { initializing = null; } })();
    return initializing;
  }
  async function login(username, password) {
    const config = await request('/auth/csrf'); configureSession(config.auth_mode, config.csrf_token); mode.value = config.auth_mode;
    const result = await request('/auth/login', { method: 'POST', body: JSON.stringify({ username, password }) });
    resetSession('login'); configureSession('password', result.csrf_token); user.value = result.user; ready.value = true;
    channel?.postMessage('changed'); return home();
  }
  async function logout() {
    try { await request('/auth/logout', { method: 'POST' }); }
    finally { resetSession('logout'); channel?.postMessage('changed'); }
  }
  async function changePassword(current_password, new_password) {
    await request('/auth/password', { method: 'POST', body: JSON.stringify({ current_password, new_password }) });
    resetSession('password'); channel?.postMessage('changed');
  }
  return { user, ready, mode, error, isSuper, manages, roleLabel, canManage, home, init, refresh, login, logout, changePassword };
});
