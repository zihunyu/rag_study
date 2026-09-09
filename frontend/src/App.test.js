import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { mount, flushPromises } from '@vue/test-utils';
import { createPinia, setActivePinia } from 'pinia';
import { createRouter, createMemoryHistory } from 'vue-router';
import App from './App.vue';
import LibrariesPage from './pages/LibrariesPage.vue';
import LibraryPage from './pages/LibraryPage.vue';
import TasksPage from './pages/TasksPage.vue';
import ChatPage from './pages/ChatPage.vue';
import DocumentPage from './pages/DocumentPage.vue';
import SystemPage from './pages/SystemPage.vue';
import AcceptancePage from './pages/AcceptancePage.vue';
import { confirmPublication } from './api.js';
import MarkdownContent from './components/MarkdownContent.vue';
import { useWorkspace } from './stores/workspace.js';
import { useUploads } from './stores/uploads.js';
import { useConversations } from './stores/conversations.js';
vi.mock('./fileHash.js', () => ({ sha256File: vi.fn(async () => 'a'.repeat(64)) }));
const spaces = [{ id: 'a', name: '产品手册', description: '产品知识', document_count: 3, answerable_count: 1, pending_count: 2, processing_count: 0, chunk_count: 8, updated_ms: 0 }, { id: 'b', name: '团队制度', description: '', document_count: 0, answerable_count: 0, pending_count: 0, processing_count: 0, chunk_count: 0, updated_ms: 0 }];
const capabilities = { accepted_extensions: ['.md'], file_mime_types: { '.md': 'text/markdown' }, max_file_size_bytes: 10000 };
const json = (body, status = 200, headers = {}) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json', ...headers } });
const documentRow = (name, state = 'pending_review') => ({ document_id: name, space_id: 'a', filename: name, version_id: `v-${name}`, version_no: 1, processing_state: 'VALIDATED', availability: state, is_answerable: state === 'available', chunk_count: 2, size_bytes: 100, available_actions: ['view', 'review_publish'], updated_at: 0 });
let wrapper, fetchMock, routes, requests;
beforeEach(() => {
  localStorage.clear(); sessionStorage.clear(); requests = [];
  HTMLDialogElement.prototype.showModal = function () { this.open = true; };
  HTMLDialogElement.prototype.close = function () { this.open = false; };
  Element.prototype.scrollIntoView = vi.fn();
  routes = new Map();
  fetchMock = vi.fn(async (url, options = {}) => {
    const target = new URL(url, 'http://localhost'); requests.push({ path: target.pathname, query: target.searchParams, options });
    const handler = routes.get(target.pathname); if (handler) return handler(target, options);
    if (target.pathname === '/api/spaces/overview') return json({ items: spaces, totals: { document_count: 3, answerable_count: 1, pending_count: 2 } });
    if (target.pathname === '/api/auth/csrf') return json({ auth_mode: 'local_single_user', csrf_token: '' });
    if (target.pathname === '/api/auth/me') return json({ id: 'local-admin', display_name: '测试管理员', global_role: 'super_admin', auth_mode: 'local_single_user', capabilities: ['admin', 'manage', 'ask'], spaces });
    if (target.pathname === '/api/capabilities') return json(capabilities);
    if (target.pathname === '/api/conversations') return json([]);
    if (target.pathname === '/api/ingestion-jobs/summary') return json({ counts: { QUEUED: 1 } });
    if (target.pathname === '/api/ingestion-jobs') return json([]);
    return json([]);
  });
  vi.stubGlobal('fetch', fetchMock);
});
afterEach(() => { wrapper?.unmount(); wrapper = null; vi.useRealTimers(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });
async function app(path = '/knowledge-bases') {
  const pinia = createPinia(); setActivePinia(pinia);
  const router = createRouter({ history: createMemoryHistory(), routes: [
    { path: '/knowledge-bases', component: LibrariesPage, meta: { section: '知识库' } },
    { path: '/knowledge-bases/:spaceId/documents/:documentId', component: DocumentPage, meta: { section: '知识库' } },
    { path: '/knowledge-bases/:spaceId/:view?', component: LibraryPage, meta: { section: '知识库' } },
    { path: '/acceptance/:spaceId?', component: AcceptancePage }, { path: '/tasks', component: TasksPage, meta: { section: '任务中心' } },
    { path: '/chat/:conversationId?', component: ChatPage, meta: { section: '知识问答' } },
    { path: '/system', component: SystemPage },
    { path: '/admin/users', component: { template: '<div />' } },
    { path: '/admin/audit', component: { template: '<div />' } },
  ] });
  await router.push(path); await router.isReady();
  wrapper = mount(App, { global: { plugins: [pinia, router] } }); await flushPromises();
  return { router, pinia };
}
describe('knowledge workspace', () => {
  it('shows real counts and administrator navigation after identity initialization', async () => {
    await app(); expect(wrapper.text()).toContain('产品手册'); expect(wrapper.findAll('.metric-number').map(el => el.text())).toEqual(['2', '3', '1READY', '2']);
    expect(wrapper.text()).not.toMatch(/登录|退出|OIDC/); expect(wrapper.findAll('.nav-item')).toHaveLength(7);
    await wrapper.get('input[aria-label="搜索知识库"]').setValue('制度'); expect(wrapper.findAll('.library-card')).toHaveLength(1);
  });
  it('creates a knowledge base and persists its description before navigation', async () => {
    routes.set('/api/spaces', (_, options) => { expect(JSON.parse(options.body).name).toBe('新品资料'); return json({ id: 'new', name: '新品资料' }, 201); });
    routes.set('/api/spaces/new', (_, options) => { expect(options.method).toBe('PATCH'); expect(JSON.parse(options.body).description).toBe('最新产品'); return json({ id: 'new' }); });
    const { router } = await app(); await wrapper.get('.page-heading .primary').trigger('click');
    await wrapper.get('#space-name').setValue('新品资料'); await wrapper.get('#space-description').setValue('最新产品'); await wrapper.get('form').trigger('submit'); await flushPromises();
    expect(router.currentRoute.value.path).toBe('/knowledge-bases/new/documents');
  });
  it('defaults to management scope and filters before cursor pagination', async () => {
    routes.set('/api/spaces/a/documents/preview', target => json(target.searchParams.has('cursor') ? [documentRow('草稿二.md')] : [documentRow('草稿一.md')], 200, target.searchParams.has('cursor') ? {} : { 'X-Next-Cursor': 'page2' }));
    await app('/knowledge-bases/a/documents'); expect(wrapper.text()).toContain('草稿一.md'); expect(wrapper.text()).toContain('待复核');
    expect(requests.find(row => row.path.endsWith('/preview')).query.get('limit')).toBe('30');
    await wrapper.get('.table-footer button').trigger('click'); await flushPromises(); expect(wrapper.findAll('tbody tr')).toHaveLength(2);
    expect(requests.find(row => row.query.get('cursor') === 'page2')).toBeTruthy();
  });
  it('ignores late document results after quickly switching knowledge bases', async () => {
    let finishA;
    routes.set('/api/spaces/a/documents/preview', () => new Promise(resolve => { finishA = resolve; }));
    routes.set('/api/spaces/b/documents/preview', () => json([documentRow('正确的文档.md')]));
    const { router } = await app('/knowledge-bases/a/documents'); await router.push('/knowledge-bases/b/documents'); await flushPromises();
    finishA(json([documentRow('过期的文档.md')])); await flushPromises();
    expect(wrapper.text()).toContain('正确的文档.md'); expect(wrapper.text()).not.toContain('过期的文档.md');
  });
  it('restores processing jobs from the server on page load', async () => {
    routes.set('/api/ingestion-jobs', () => json([{ id: 'j1', filename: '处理中.md', space_id: 'a', document_id: 'd', state: 'QUEUED', attempt: 0, max_attempts: 4, available_actions: ['cancel'] }]));
    await app('/tasks'); expect(wrapper.text()).toContain('处理中.md'); expect(wrapper.text()).toContain('等待处理');
    expect(requests.some(row => row.path === '/api/ingestion-jobs')).toBe(true);
  });
  it('does not render HTML from document contents', () => {
    wrapper = mount(MarkdownContent, { props: { text: '<img src=x onerror=alert(1)>\n[bad](javascript:alert(1))\n\n| A | B |\n|---|---|\n| one | two |' } });
    expect(wrapper.find('img').exists()).toBe(false); expect(wrapper.find('a').exists()).toBe(false); expect(wrapper.find('table').exists()).toBe(true);
  });

  it('waits for actual quality summaries before enabling batch publication', async () => {
    let finish;
    routes.set('/api/spaces/a/documents/preview', () => json([documentRow('policy.md')]));
    routes.set('/api/document-versions/v-policy.md/quality-report', () => new Promise(resolve => { finish = resolve; }));
    await app('/knowledge-bases/a/documents');
    await wrapper.get('input[aria-label="选择 policy.md"]').setValue(true);
    await wrapper.findAll('button').find(b => b.text() === '检查并批量发布').trigger('click');
    const confirm = () => wrapper.findAll('button').find(b => b.text() === '已检查质量，确认发布');
    expect(confirm().element.disabled).toBe(true);
    expect(wrapper.text()).toContain('读取质量报告');
    finish(json({ node_count: 12, locator_coverage: 0.75, issue_codes: ['LOCATOR_MISSING'], disposition: 'READY_FOR_REVIEW' }));
    await flushPromises();
    expect(wrapper.text()).toContain('12 个内容节点'); expect(wrapper.text()).toContain('75%');
    expect(confirm().element.disabled).toBe(false);
    expect(requests.some(r => r.path.endsWith(':review-and-publish'))).toBe(false);
  });

  it('opens the cited historical version and finds a chunk beyond the first page', async () => {
    routes.set('/api/spaces/a/documents/policy/workspace', () => json({ ...documentRow('policy'), current_version_id: 'old', version_id: 'new', versions: [{ id: 'old', version_no: 1, original_key: 'version/1/original/policy-v1.md' }, { id: 'new', version_no: 2 }], unavailability_reasons: [] }));
    routes.set('/api/document-versions/old/chunks/preview', target => target.searchParams.has('cursor') ? json([{ chunk_id: 'target', text: '这是旧版本的来源', locator: { page: 9 } }]) : json(Array.from({ length: 100 }, (_, i) => ({ chunk_id: `c${i}`, text: `段落 ${i}`, locator: {} })), 200, { 'X-Next-Cursor': 'after100' }));
    routes.set('/api/document-versions/old/quality-report', () => json({ node_count: 101, locator_coverage: 1, issue_codes: [], disposition: 'READY_FOR_REVIEW' }));
    await app('/knowledge-bases/a/documents/policy?version=old&chunk=target'); await flushPromises();
    expect(wrapper.get('select[aria-label="文档版本"]').element.value).toBe('old');
    expect(wrapper.get('#chunk-target').classes()).toContain('focused');
    expect(wrapper.get('h1').text()).toBe('policy-v1.md');
    expect(requests.some(r => r.query.get('cursor') === 'after100')).toBe(true);
    expect(requests.some(r => r.path.includes('/new/chunks'))).toBe(false);
  });

  it('passes an AbortSignal when the system refresh button supplies a DOM event', async () => {
    routes.set('/api/system/status', (_, options) => {
      expect(options.signal).toBeInstanceOf(AbortSignal);
      return json({ status: 'ready', checked_at: 1, dependencies: { mysql: 'ready' }, worker: { reason: '没有探针' }, parser: { state: 'configured' }, degraded_reasons: [] });
    });
    await app('/system');
    await wrapper.findAll('button').find(b => b.text() === '重新检测').trigger('click'); await flushPromises();
    expect(requests.filter(r => r.path === '/api/system/status')).toHaveLength(2);
    expect(wrapper.text()).toContain('核心依赖检查通过');
  });

  it('keeps a confirmed lifecycle command bound to its original document during navigation', async () => {
    let finishSnapshot;
    for (const id of ['first', 'second']) {
      routes.set(`/api/spaces/a/documents/${id}/workspace`, () => json({ ...documentRow(id, 'available'), available_actions: ['view', 'revoke'], version_id: `v-${id}`, versions: [{ id: `v-${id}`, version_no: 1 }], unavailability_reasons: [] }));
      routes.set(`/api/document-versions/v-${id}/quality-report`, () => json({ node_count: 1, locator_coverage: 1, issue_codes: [], disposition: 'READY_FOR_REVIEW' }));
    }
    routes.set('/api/documents/first/lifecycle', () => new Promise(resolve => { finishSnapshot = resolve; }));
    routes.set('/api/documents/first:revoke', () => json({ lifecycle_state: 'REVOKED' }));
    const { router } = await app('/knowledge-bases/a/documents/first');
    await wrapper.get('button[aria-label="文档操作"]').trigger('click');
    await wrapper.findAll('button').find(b => b.text() === '撤回文档').trigger('click');
    await wrapper.get('dialog .dialog-actions .primary').trigger('click'); await flushPromises();
    await router.push('/knowledge-bases/a/documents/second'); await flushPromises();
    finishSnapshot(json({ row_version: 2 })); await flushPromises();
    expect(requests.filter(r => r.path.endsWith(':revoke')).map(r => r.path)).toEqual(['/api/documents/first:revoke']);
    expect(wrapper.get('h1').text()).toBe('second');
  });
});

it('resumes a publication attempt across refreshes but uses a new key after a completed publication', async () => {
  const keys = [], comments = [];
  routes.set('/api/document-versions/v:review-and-publish', (_, options) => {
    keys.push(options.headers.get('Idempotency-Key')); comments.push(JSON.parse(options.body).comment);
    return json({ phase: keys.length === 1 ? 'reviewed' : 'published' });
  });
  expect((await confirmPublication('v', '首次检查')).phase).toBe('reviewed');
  await confirmPublication('v', '刷新后的文案');
  expect(keys[1]).toBe(keys[0]); expect(comments[1]).toBe('首次检查');
  expect(sessionStorage.getItem('ragspace-publication:v')).toBeNull();
  await confirmPublication('v', '撤回后重新发布');
  expect(keys[2]).not.toBe(keys[1]);
});
describe('upload persistence and isolation', () => {
  it('continues valid files after a rejected file and limits simultaneous uploads to two', async () => {
    const pinia = createPinia(); setActivePinia(pinia); useWorkspace().capabilities = capabilities;
    let active = 0, maxActive = 0, serial = 0; const callbacks = [];
    class XHR {
      upload = {}; status = 200; responseText = '{"row_version":2}'; open() {} setRequestHeader() {}
      send() { active++; maxActive = Math.max(maxActive, active); callbacks.push(() => { active--; this.onload(); }); }
    }
    vi.stubGlobal('XMLHttpRequest', XHR);
    routes.set('/api/spaces/a/upload-sessions', () => json({ upload_session_id: `u${++serial}`, upload_path: `/api/upload-sessions/u${serial}/content`, row_version: 1 }));
    for (let i = 1; i <= 3; i++) { routes.set(`/api/upload-sessions/u${i}`, () => json({ state: 'CREATED' })); routes.set(`/api/upload-sessions/u${i}:complete`, () => json({ document_id: `d${i}`, document_version_id: `v${i}`, job_id: `j${i}` })); }
    const store = useUploads(); store.enqueue([new File(['x'], 'bad.exe'), ...[1, 2, 3].map(i => new File(['a'], `valid${i}.md`))], 'a'); await flushPromises();
    expect(active).toBe(2); expect(store.items.find(row => row.filename === 'bad.exe').state).toBe('failed');
    callbacks.shift()(); await flushPromises(); expect(active).toBe(2);
    while (callbacks.length) { callbacks.shift()(); await flushPromises(); }
    expect(maxActive).toBe(2); expect(store.items.filter(row => row.state === 'submitted')).toHaveLength(3);
  });
  it('recovers a completed server upload after a lost response without uploading again', async () => {
    sessionStorage.setItem('ragkb.uploads', JSON.stringify([{ id: 'local', uploadSessionId: 'saved', state: 'completing', filename: 'saved.md', spaceId: 'a' }]));
    routes.set('/api/upload-sessions/saved', () => json({ state: 'COMPLETED', document_id: 'd', document_version_id: 'v', job_id: 'job' }));
    setActivePinia(createPinia()); const uploads = useUploads(); await uploads.restore();
    expect(uploads.items[0]).toMatchObject({ state: 'submitted', jobId: 'job' }); expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});
describe('conversation result gating', () => {
  it.each([
    ['MODEL_PROVIDER_RATE_LIMITED', 429, '上游模型返回 HTTP 429'],
    ['MODEL_PROVIDER_TIMEOUT', undefined, '解析本轮问题时超时'],
    ['MODEL_ACCOUNT_REQUEST_EXCEEDS_TOKEN_BUDGET', undefined, '超过账户单次可用预算'],
  ])('explains a failure before retrieval with its request reference (%s)', async (code, http_status, message) => {
    const result = { status: 'system_error', verified: false, answer: 'PRIVATE DRAFT', citations: [], warnings: [code], coverage_report: { execution_failure: { stage: 'conversation_context', code, http_status, request_id: 'context-request' }, performance: { elapsed_seconds: 1, events: [{ kind: 'stage', name: 'conversation.resolve', seconds: 1, status: 'failed' }] } } };
    routes.set('/api/conversations/c', () => json({ conversation: { id: 'c', space_id: 'a', title: '会话失败' }, turns: [{ id: 't', sequence_number: 1, original_question: '问题', state: 'failed', error_code: code, result }], next_before: null }));
    await app('/chat/c'); await flushPromises();
    expect(wrapper.get('.answer-notice').text()).toContain(message);
    expect(wrapper.get('.answer-notice').text()).toContain('尚未开始知识检索');
    expect(wrapper.text()).toContain('context-request');
    expect(wrapper.text()).not.toContain('PRIVATE DRAFT');
  });
  it('explains a legacy context rate limit even without a RAG result', async () => {
    routes.set('/api/conversations/c', () => json({ conversation: { id: 'c', space_id: 'a', title: '旧记录' }, turns: [{ id: 't', sequence_number: 1, original_question: '问题', state: 'failed', error_code: 'ProviderRateLimited', result: null }], next_before: null }));
    await app('/chat/c'); await flushPromises();
    expect(wrapper.get('.answer-notice').text()).toContain('限流或额度限制');
    expect(wrapper.get('.answer-notice').text()).not.toContain('资料不足');
  });
  it.each([
    [['CLAIM_VERIFIER_PROTOCOL_INVALID', 'VERIFIER_CONDITION_BUDGET_EXCEEDED'], '超过本轮处理容量'],
    [['CLAIM_VERIFIER_UNAVAILABLE', 'CLAIM_VERIFIER_TIMEOUT'], '答案核验超时'],
    [['CLAIM_VERIFIER_UNAVAILABLE'], '答案核验服务暂时不可用'],
    [['CLAIM_VERIFIER_PROTOCOL_INVALID', 'VERIFIER_VERDICT_COUNT_INVALID'], '结论数量或编号'],
    [['CLAIM_VERIFIER_PROTOCOL_INVALID', 'VERIFIER_CONDITION_REPAIR_BUDGET_EXCEEDED'], '补全所需资料超过本轮预算'],
    [['CLAIM_VERIFIER_PROTOCOL_INVALID', 'MODEL_PROVIDER_MODEL_UNSUPPORTED'], '不支持配置的模型'],
    [['CLAIM_VERIFIER_PROTOCOL_INVALID', 'MODEL_PROVIDER_HTTP_ERROR'], '核验接口拒绝了请求'],
    [['ANSWER_NOT_SUPPORTED'], '有内容未通过原文核验'],
    [['ANSWER_NOT_SUPPORTED', 'ANSWER_CITATION_COVERAGE_INVALID'], '事实与引用未能完整对应'],
  ])('prioritizes the specific verification failure over generic state (%s)', async (warnings, message) => {
    routes.set('/api/conversations/c', () => json({ conversation: { id: 'c', space_id: 'a', title: '容量测试' }, turns: [{ id: 't', sequence_number: 1, original_question: '问题', state: 'failed', result: { rag_run_id: 'failed-batch', status: 'system_error', verified: false, answer: 'PRIVATE DRAFT', citations: [], warnings } }], next_before: null }));
    await app('/chat/c'); await flushPromises();
    expect(wrapper.get('.answer-notice').text()).toContain(message);
    expect(wrapper.get('.technical').text()).toContain('failed-batch');
    expect(wrapper.text()).not.toContain('PRIVATE DRAFT');
  });
  it('shows a verifier failure and run reference even when the turn has no error_code', async () => {
    routes.set('/api/conversations/c', () => json({ conversation: { id: 'c', space_id: 'a', title: '跨文件测试' }, turns: [{ id: 't', sequence_number: 1, original_question: '综合三份资料', state: 'completed', result: { rag_run_id: 'qa-failed-run', status: 'system_error', verified: false, answer: 'NEVER RELEASE DRAFT', citations: [], warnings: ['CLAIM_VERIFIER_PROTOCOL_INVALID', 'VERIFIER_CONDITION_WITNESS_INVALID'] } }], next_before: null }));
    await app('/chat/c'); await flushPromises();
    expect(wrapper.get('.answer-notice').text()).toContain('已找到相关资料，但答案核验未完成');
    expect(wrapper.get('.technical').text()).toContain('qa-failed-run');
    expect(wrapper.get('.technical').text()).toContain('VERIFIER_CONDITION_WITNESS_INVALID');
    expect(wrapper.text()).not.toContain('NEVER RELEASE DRAFT');
    expect(wrapper.find('.verified-label').exists()).toBe(false);
  });
  it('shows the failed condition batch without revealing diagnostic content', async () => {
    routes.set('/api/conversations/c', () => json({ conversation: { id: 'c', space_id: 'a', title: '分批核验' }, turns: [{ id: 't', sequence_number: 1, original_question: '问题', state: 'completed', result: { rag_run_id: 'batch-timeout', status: 'system_error', verified: false, answer: null, citations: [], warnings: ['CLAIM_VERIFIER_TIMEOUT'], coverage_report: { verification_failure: { stage: 'conditions', batch_number: 3, batch_count: 6, completed_batches: 2, condition_count: 93 } } } }], next_before: null }));
    await app('/chat/c'); await flushPromises();
    expect(wrapper.get('.technical').text()).toContain('第 3 / 6 批');
    expect(wrapper.get('.technical').text()).toContain('已完成 2 批，共 93 条条件');
    expect(wrapper.find('.verified-label').exists()).toBe(false);
  });
  it('explains unrelated images even when abstention itself passed verification', async () => {
    routes.set('/api/conversations/c', () => json({ conversation: { id: 'c', space_id: 'a', title: '产品 B' }, turns: [{ id: 't', sequence_number: 1, original_question: '产品 B 功率', state: 'completed', result: { status: 'insufficient_evidence', verified: true, answer: null, citations: [], warnings: ['VISUAL_EVIDENCE_EXCLUDED:not_relevant'] } }], next_before: null }));
    await app('/chat/c'); await flushPromises();
    expect(wrapper.get('.answer-notice').text()).toContain('找到的图片未提供问题所需的信息');
    expect(wrapper.find('.verified-label').exists()).toBe(false);
  });
  it.each([false, true])('explains current and older saved upstream rate limits (legacy=%s)', async legacy => {
    const result = { status: 'system_error', verified: false, answer: 'PRIVATE DRAFT', citations: [],
      warnings: legacy ? ['CLAIM_VERIFIER_UNAVAILABLE'] : ['MODEL_PROVIDER_RATE_LIMITED'],
      coverage_report: legacy ? { performance: { events: [{ kind: 'model_http', outcome: '429' }] } } : {} };
    routes.set('/api/conversations/c', () => json({ conversation: { id: 'c', space_id: 'a', title: '限流诊断' }, turns: [{ id: 't', sequence_number: 1, original_question: '问题', state: 'failed', result }], next_before: null }));
    await app('/chat/c'); await flushPromises();
    expect(wrapper.get('.answer-notice').text()).toContain('上游模型服务触发限流（HTTP 429）');
    expect(wrapper.text()).not.toContain('PRIVATE DRAFT');
  });
  it('explains rejected image evidence without displaying the rejected answer', async () => {
    routes.set('/api/conversations/c', () => json({ conversation: { id: 'c', space_id: 'a', title: '图片参数' }, turns: [{ id: 't', conversation_id: 'c', sequence_number: 1, original_question: '功率多少', state: 'completed', result: { status: 'INSUFFICIENT_EVIDENCE', verified: false, answer: '错误的旧文字 999 W', citations: [], warnings: ['VISUAL_EVIDENCE_EXCLUDED:conflict'] } }], next_before: null }));
    await app('/chat/c'); await flushPromises();
    expect(wrapper.get('.answer-notice').text()).toContain('图片与旧识别文字存在冲突');
    expect(wrapper.text()).not.toContain('999 W');
    expect(wrapper.find('.citation-button').exists()).toBe(false);
  });
  it('renders a verified summary and table with working inline source links', async () => {
    const citation = { evidence_id: 'E2', source_url: '/api/test-source', filename: '设备参数.csv', version_no: 2, version_id: 'v2', document_id: 'doc', chunk_id: 'chunk', locator: { row: 2 } };
    routes.set('/api/conversations/c', () => json({ conversation: { id: 'c', space_id: 'a', title: '设备功率' }, turns: [{ id: 't', conversation_id: 'c', sequence_number: 1, original_question: '总结功率', state: 'completed', result: { verified: true, answer: '功率随环境变化。[E2]\n\n| 环境 | 功率 |\n| --- | --- |\n| 常温 | **420 W** [E2] |\n| 低温 | 390 W [E2] |', citations: [citation] } }], next_before: null }));
    routes.set('/api/test-source', () => json({ text: '常温 420 W，低温 390 W。' }));
    await app('/chat/c'); await flushPromises();
    expect(wrapper.findAll('.assistant-body table tbody tr')).toHaveLength(2);
    expect(wrapper.get('.assistant-body strong').text()).toBe('420 W');
    const link = wrapper.findAll('.assistant-body a[href="#citation-E2"]')[1];
    expect(link.text()).toBe('1');
    await link.trigger('click'); await flushPromises();
    expect(wrapper.get('.source-text').text()).toContain('常温 420 W');
    expect(wrapper.get('.source-file').text()).toContain('设备参数.csv');
  });
  it('retains failed state and never releases an unverified answer from SSE', async () => {
    setActivePinia(createPinia()); const store = useConversations();
    routes.set('/api/conversations/c', () => json({ conversation: { id: 'c', space_id: 'a' }, turns: [], next_before: null }));
    routes.set('/api/conversations/c/turns:stream', () => new Response(`event: result\ndata: ${JSON.stringify({ id: 't', conversation_id: 'c', sequence_number: 1, original_question: 'test', state: 'failed', result: { verified: false, answer: 'NEVER DISPLAY', citations: [{ evidence_id: 'E1' }] } })}\n\n`, { headers: { 'Content-Type': 'text/event-stream' } }));
    await store.activate('c'); await store.send('test'); expect(store.turns[0].result.answer).toBeNull(); expect(store.turns[0].result.citations).toEqual([]);
  });
  it('does not mix responses after changing conversations', async () => {
    setActivePinia(createPinia()); const store = useConversations(); let finish;
    routes.set('/api/conversations/a', () => new Promise(resolve => { finish = resolve; }));
    routes.set('/api/conversations/b', () => json({ conversation: { id: 'b', space_id: 'b' }, turns: [], next_before: null }));
    const slow = store.activate('a'); await store.activate('b'); finish(json({ conversation: { id: 'a', space_id: 'a' }, turns: [{ id: 'bad', sequence_number: 1 }], next_before: null })); await slow;
    expect(store.current.id).toBe('b'); expect(store.turns).toEqual([]);
  });
});
