import { beforeEach, afterEach, it, expect, vi } from 'vitest';
import { createPinia, setActivePinia } from 'pinia';
import { mount, flushPromises } from '@vue/test-utils';
import { createMemoryHistory, createRouter } from 'vue-router';
import { configureSession, resetSession, sessionFetch } from './authTransport.js';
import { request } from './api.js';
import { useAuth } from './stores/auth.js';
import { useWorkspace } from './stores/workspace.js';
import { useConversations } from './stores/conversations.js';
import LoginPage from './pages/LoginPage.vue';
import AuditPage from './pages/AuditPage.vue';
import VisualAsset from './components/VisualAsset.vue';

let wrapper;
const json = (data, status = 200) => new Response(JSON.stringify(data), { status });
beforeEach(() => { setActivePinia(createPinia()); configureSession('password', 'fixture-csrf'); });
afterEach(() => { wrapper?.unmount(); wrapper = null; resetSession('test'); configureSession('', ''); vi.unstubAllGlobals(); vi.restoreAllMocks(); });

it('adds CSRF and cookies without using a localStorage login token', async () => {
  const transport = vi.fn(async () => json({ ok: true }));
  await sessionFetch('/api/upload', { method: 'PUT', body: 'bytes' }, transport);
  const options = transport.mock.calls[0][1];
  expect(options.headers.get('X-CSRF-Token')).toBe('fixture-csrf');
  expect(options.credentials).toBe('include');
  expect(localStorage.getItem('ragkb_session')).toBeNull();
});

it('does not submit a write when CSRF has been cleared', async () => {
  resetSession('logout'); const transport = vi.fn();
  await expect(sessionFetch('/api/upload', { method: 'PUT' }, transport)).rejects.toThrow('SESSION_CSRF_UNAVAILABLE');
  expect(transport).not.toHaveBeenCalled();
});

it('drops JSON that finishes after account switching', async () => {
  let finish; const reading = new Promise(resolve => { finish = resolve; });
  const transport = vi.fn(async () => ({ ok: true, status: 200, headers: new Headers(), json: () => reading }));
  const pending = request('/spaces', {}, transport);
  await flushPromises(); resetSession('changed'); finish({ secret: 'old-account-data' });
  await expect(pending).rejects.toMatchObject({ name: 'AbortError' });
});

it('clears histories, pending submissions and library selection on logout', () => {
  const workspace = useWorkspace(), chats = useConversations();
  workspace.spaces = [{ id: 'private' }]; workspace.select('private');
  chats.current = { id: 'old' }; chats.turns = [{ original_question: 'private' }];
  sessionStorage.setItem('ragkb.pending-turn.old', 'private question');
  resetSession('logout');
  expect(workspace.spaces).toEqual([]); expect(chats.turns).toEqual([]);
  expect(chats.current).toBeNull(); expect(workspace.selectedId).toBe('');
  expect(sessionStorage.getItem('ragkb.pending-turn.old')).toBeNull();
});

it('does not promote management of A into management of B', () => {
  const auth = useAuth();
  auth.user = { id: 'mixed', global_role: 'member', capabilities: ['manage', 'ask'], spaces: [{ id: 'A', my_role: 'manager' }, { id: 'B', my_role: 'qa' }] };
  expect(auth.canManage('A')).toBe(true); expect(auth.canManage('B')).toBe(false);
  expect(auth.canManage('C')).toBe(false); expect(auth.home()).toBe('/knowledge-bases/A/documents');
});

it('refreshing revoked permissions clears the old conversation before presenting the new identity', async () => {
  const auth = useAuth(), chats = useConversations();
  auth.user = { id: 'reader', auth_revision: 1, spaces: [{ id: 'A', my_role: 'qa' }] };
  chats.current = { id: 'old' }; chats.turns = [{ original_question: 'private' }];
  vi.stubGlobal('fetch', vi.fn(async () => json({ id: 'reader', auth_revision: 2, spaces: [] })));
  await auth.refresh();
  expect(auth.user.auth_revision).toBe(2); expect(chats.current).toBeNull();
  expect(chats.turns).toEqual([]);
});

it('renders password login without a role chooser or knowledge metadata', async () => {
  const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/login', component: LoginPage }] });
  await router.push('/login'); await router.isReady();
  wrapper = mount(LoginPage, { global: { plugins: [router] } });
  expect(wrapper.find('select').exists()).toBe(false);
  expect(wrapper.find('#password').attributes('type')).toBe('password');
  await wrapper.get('[aria-label="显示密码"]').trigger('click');
  expect(wrapper.find('#password').attributes('type')).toBe('text');
  expect(wrapper.find('nav').exists()).toBe(false);
});

it('does not put a late operation record in the conversation audit table', async () => {
  let finish;
  vi.stubGlobal('fetch', vi.fn(url => String(url).includes('/conversation-audits')
    ? Promise.resolve(json({ items: [{ id: 'conversation', username: 'correct owner', space_name: 'A' }] }))
    : new Promise(resolve => { finish = resolve; })));
  wrapper = mount(AuditPage); await flushPromises();
  await wrapper.findAll('button').find(b => b.text() === '问答审计').trigger('click');
  await flushPromises(); finish(json({ items: [{ id: 'operation', actor_id: 'wrong row' }] })); await flushPromises();
  expect(wrapper.text()).toContain('correct owner'); expect(wrapper.text()).not.toContain('wrong row');
});

it('releases source image blobs when the account session ends', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => new Response(new Blob(['fixture']))));
  URL.createObjectURL = vi.fn(() => 'blob:source-fixture'); URL.revokeObjectURL = vi.fn();
  wrapper = mount(VisualAsset, { props: { compact: true, asset: { id: 'a', status: 'verified', image_url: '/api/citation/image' } } });
  await flushPromises();
  expect(wrapper.get('img').attributes('src')).toBe('blob:source-fixture');
  resetSession('logout'); await flushPromises();
  expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:source-fixture');
  expect(wrapper.get('img').attributes('src')).toBeUndefined();
});
