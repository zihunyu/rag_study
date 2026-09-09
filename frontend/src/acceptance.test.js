import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { mount, flushPromises } from '@vue/test-utils';
import { createMemoryHistory, createRouter } from 'vue-router';
import AcceptancePage from './pages/AcceptancePage.vue';
import { request, requestPage, command } from './api.js';

vi.mock('./api.js', () => ({ request: vi.fn(), requestPage: vi.fn(), command: vi.fn() }));
vi.mock('./stores/auth.js', () => ({ useAuth: () => ({ canManage: id => ['a','b'].includes(id), isSuper: false }) }));
vi.mock('./stores/workspace.js', () => ({ useWorkspace: () => ({ spaces: [{ id: 'a', name: '产品资料' }, { id: 'b', name: '另一知识库' }] }) }));
const payload = { key: 'Q01', category: '单文件', question: '保修多久？', reading: { mode: 'fact', document_ids: ['d1'] },
  history: [], expected_status: 'answered', required_points: ['三年及起算日期'], forbidden_claims: ['终身保修'],
  required_source_documents: ['d1'], minimum_distinct_cited_documents: 1, source_notes: '保修条款原文', review_state: 'confirmed', archived: false };
const caseRow = { id: 'c1', revision: 1, payload };
const run = { id: 'r1', state: 'completed', call_limit: 20, calls_reserved: 4, payload: { name: '版本 A', cases: [caseRow],
  snapshot: { config: { llm_model: 'fixture-model' }, sources: [], code_revision: 'a'.repeat(64) } },
  usage: { observed_calls: 4, input_tokens: 100, output_tokens: 30, unknown_usage_calls: 0, unpriced_calls: 4, known_cost_cny: 0 },
  attempts: [{ id: 't1', case_id: 'c1', attempt: 1, state: 'completed', verdict: 'pending_review', reviews: [],
    payload: { checks: [{ name: '必要来源齐全', passed: true, detail: '已引用' }], steps: [{ result: { status: 'answered', answer: '保修三年。', citations: [{ evidence_id: 'E1' }], verified: true }, evidence: [], failure: {} }] } }] };
let wrapper;
beforeEach(() => {
  vi.clearAllMocks();
  HTMLDialogElement.prototype.showModal = function () { this.open = true; };
  HTMLDialogElement.prototype.close = function () { this.open = false; };
  request.mockImplementation(async path => {
    if (path.endsWith('/cases')) return [structuredClone(caseRow)];
    if (path.endsWith('/runs')) return [{ id: 'r1', name: '版本 A', state: 'completed', created_at: 1, case_count: 1 }];
    if (path.endsWith('/runs/r1')) return structuredClone(run);
    return {};
  });
  requestPage.mockResolvedValue({ items: [{ document_id: 'd1', filename: '保修条款.md' }], nextCursor: null });
  command.mockResolvedValue({});
});
afterEach(() => wrapper?.unmount());
async function open(path = '/acceptance/a') {
  const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/acceptance/:spaceId?', component: AcceptancePage }] });
  await router.push(path); await router.isReady();
  wrapper = mount({ template: '<RouterView/>' }, { global: { plugins: [router] } });
  await flushPromises(); return router;
}
const button = text => wrapper.findAll('button').find(b => b.text() === text);

describe('问答验收工作台', () => {
  it('explains the total deadline while retaining the failed attempt', async () => {
    const paused = structuredClone(run);
    paused.state = 'paused';
    paused.reason = 'REQUEST_DEADLINE_EXCEEDED';
    paused.attempts[0].state = 'incomplete';
    paused.attempts[0].verdict = 'incomplete';
    request.mockImplementation(async path => {
      if (path.endsWith('/cases')) return [structuredClone(caseRow)];
      if (path.endsWith('/runs')) return [{ id: 'r1', name: '版本 A', state: 'paused', created_at: 1, case_count: 1 }];
      if (path.endsWith('/runs/r1')) return paused;
      return {};
    });
    await open('/acceptance/a?run=r1');
    expect(wrapper.text()).toContain('本题处理超过总时限，已保存收到的结果与诊断');
    expect(button('继续未完成题')).toBeDefined();
    expect(button('第 1 次')).toBeDefined();
  });
  it('edits reactive case data and preserves the revision on save', async () => {
    await open(); await button('编辑').trigger('click');
    expect(wrapper.find('[role=dialog]').exists()).toBe(true);
    const question = wrapper.findAll('textarea')[0]; await question.setValue('保修多久，从哪天起算？');
    await wrapper.get('form').trigger('submit'); await flushPromises();
    const update = request.mock.calls.find(([, options]) => options?.method === 'PUT');
    expect(JSON.parse(update[1].body)).toMatchObject({ revision: 1, case: { question: '保修多久，从哪天起算？' } });
  });
  it('requires point and source review instead of treating system verification as a pass', async () => {
    await open('/acceptance/a?run=r1'); await button('第 1 次').trigger('click');
    expect(wrapper.text()).toContain('待人工复核');
    expect(button('确认通过').attributes('disabled')).toBeDefined();
    const checks = wrapper.findAll('[role=dialog] input[type=checkbox]');
    for (const check of checks) await check.setValue(true);
    await wrapper.get('[role=dialog] textarea').setValue('原文与三年及起算要求一致，没有终身保修说法。');
    expect(button('确认通过').attributes('disabled')).toBeUndefined();
    command.mockResolvedValueOnce({ reviewed: true, review: { id: 'review-1', verdict: 'passed', note: '已核对原文', created_at: 1 } });
    await button('确认通过').trigger('click'); await flushPromises();
    expect(command).toHaveBeenCalledWith('/spaces/a/acceptance/runs/r1/attempts/t1/reviews', expect.objectContaining({ checked_points: [0, 1], sources_checked: true }));
    expect(wrapper.text()).not.toContain('管理员诊断');
    expect(wrapper.text()).toContain('结果：通过。');
  });
  it('does not publish late case results after switching knowledge bases', async () => {
    let complete;
    request.mockImplementation(path => path === '/spaces/a/acceptance/cases'
      ? new Promise(resolve => { complete = resolve; }) : Promise.resolve([]));
    const router = await open();
    await router.push('/acceptance/b'); await flushPromises();
    complete([caseRow]); await flushPromises();
    expect(wrapper.text()).not.toContain('保修多久？');
  });
  it('creates a run without starting paid calls until the user clicks start', async () => {
    await open(); await wrapper.get('input[aria-label="选择 Q01"]').setValue(true);
    command.mockResolvedValue({ id: 'r1' });
    await button('创建运行').trigger('click'); await flushPromises();
    expect(command).toHaveBeenCalledTimes(1);
    expect(command.mock.calls[0][0]).toBe('/spaces/a/acceptance/runs');
  });
});
