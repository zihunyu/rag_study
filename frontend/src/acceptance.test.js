import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { mount, flushPromises } from '@vue/test-utils';
import { createMemoryHistory, createRouter } from 'vue-router';
import AcceptancePage from './pages/AcceptancePage.vue';
import ParsingAcceptancePane from './components/ParsingAcceptancePane.vue';
import AcceptanceLineage from './components/AcceptanceLineage.vue';
import AcceptancePerformance from './components/AcceptancePerformance.vue';
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
  it('distinguishes nested timings, failed calls and missing historical identity', () => {
    wrapper = mount(AcceptancePerformance, { props: { summary: { elapsed_seconds: 100, stages: [{ key: 'verification', label: '首次核验', seconds: 60 }], other_seconds: 40, reverification_count: 1, failed_calls: 1, same_request_again_count: 0, cache: [{ cache: 'verified_result', outcome: 'bypass_acceptance' }], calls: [{ number: 1, model: 'fixture', outcome: 'failed', network_seconds: 60, queue_seconds: 0, sent: true, identity_recorded: false }] } } });
    expect(wrapper.text()).toContain('首次核验');
    expect(wrapper.text()).toContain('阶段未记录');
    expect(wrapper.text()).toContain('并行调用耗时不能相加');
    expect(wrapper.text()).toContain('验收主动绕过答案缓存');
  });
  it('requires independent relevance review even when all required facts pass', async () => {
    const data = structuredClone(run);
    data.payload.cases[0].payload = { ...payload, criteria: [{ id: 'P1', text: '三年', sources: [] }], required_points: [], forbidden_claims: [], check_relevance: true };
    data.attempts[0].point_results = [{ point_id: 'P1', status: 'covered', answer_quote: '保修三年。', note: '原文支持' }, { point_id: '__answer_relevance', status: 'incorrect', answer_quote: '保修三年。', note: '需要负责人确认范围' }];
    const original = request.getMockImplementation();
    request.mockImplementation(async (path, options) => path.endsWith('/runs/r1') ? data : original(path, options));
    await open('/acceptance/a?run=r1'); await button('第 1 轮 · 尝试 1').trigger('click');
    expect(wrapper.text()).toContain('回答相关性与重复附加内容');
    expect(button('确认通过').attributes('disabled')).toBeDefined();
    expect(wrapper.findAll('select').some(s => s.element.value === 'incorrect')).toBe(true);
  });
  it('creates original-file standards without copying parsed text into the gold field', async () => {
    request.mockImplementation(async path => path.includes('/versions/preview') ? [{ id: 'v1', version_no: 1 }] : path.includes('/parsing/versions/') ? { parsed: [{ id: 'n1', text: '解析器输出不能自动成为标准', locator: { page: 1 } }], chunks: [], mime_type: 'application/pdf' } : []);
    command.mockResolvedValue({ id: 'standard-1', revision: 1 });
    wrapper = mount(ParsingAcceptancePane, { props: { space: 'a', documents: [{ document_id: 'd1', filename: '原文件.pdf' }] }, global: { stubs: { OriginalFilePreview: true } } });
    await flushPromises(); await wrapper.get('[aria-label="解析验收文件"]').setValue('d1'); await flushPromises();
    await button('并排查看原文件与解析').trigger('click'); await flushPromises();
    expect(wrapper.findComponent({ name: 'OriginalFilePreview' }).exists()).toBe(true);
    await button('添加页面标准').trigger('click');
    expect(wrapper.get('[aria-label="解析标准 1 原文"]').element.value).toBe('');
    await wrapper.get('[aria-label="解析标准 1 说明"]').setValue('核对十项中的第五项');
    await wrapper.get('[aria-label="解析标准 1 原文"]').setValue('按原文件独立填写的第五项');
    await wrapper.get('[aria-label="原文件核对说明"]').setValue('已目视核对原文件');
    await wrapper.get('[aria-label="已核对原文件"]').setValue(true);
    await button('保存原文件标准').trigger('click'); await flushPromises();
    expect(command.mock.calls[0][1].standard.checks[0].quote).toBe('按原文件独立填写的第五项');
    expect(JSON.stringify(command.mock.calls[0])).not.toContain('解析器输出不能自动成为标准');
  });
  it('shows missing input capture separately from a semantic omission', () => {
    const stages = Object.fromEntries(['original','parsed','chunks','retrieval','model_input','draft','final'].map(s => [s, { status: s === 'model_input' ? 'unrecorded' : 'found', matches: [] }]));
    stages.final.semantic_status = 'missing';
    wrapper = mount(AcceptanceLineage, { props: { report: { note: '未记录不能推测', rows: [{ point_id: 'P5', text: '第五项条件', stages, first_gap: 'model_input' }] } } });
    expect(wrapper.text()).toContain('实际生成输入');
    expect(wrapper.text()).toContain('未记录');
    expect(wrapper.text()).toContain('语义遗漏');
    expect(wrapper.text()).toContain('第五项条件');
  });
  it('saves source-bound criteria and optional list checks from the simplified editor', async () => {
    const original = request.getMockImplementation();
    request.mockImplementation(async (path, options) => path.includes('/sources/')
      ? { document_id: 'd1', version_id: 'v1', next_offset: null, items: [{ document_id: 'd1', version_id: 'v1', chunk_id: 'chunk1', text: '必须核对全部十项。', locator: { page: 9 } }] }
      : original(path, options));
    await open(); await button('新建案例').trigger('click');
    await wrapper.get('[aria-label="案例问题"]').setValue('有哪些检查步骤？');
    await button('添加验收要点').trigger('click');
    await wrapper.get('[aria-label="要点 1"]').setValue('保留全部十项');
    await button('关联原文').trigger('click');
    await wrapper.get('[aria-label="依据文件"]').setValue('d1'); await flushPromises();
    await button('使用此段原文').trigger('click');
    await wrapper.get('[aria-label="预期条目数"]').setValue('10');
    await wrapper.get('form').trigger('submit'); await flushPromises();
    const write = request.mock.calls.find(([, options]) => options?.method === 'POST');
    const data = JSON.parse(write[1].body).case;
    expect(data.criteria).toHaveLength(1);
    expect(data.criteria[0].sources[0]).toMatchObject({ document_id: 'd1', version_id: 'v1', chunk_id: 'chunk1', quote: '必须核对全部十项。' });
    expect(data.list_checks.expected_count).toBe(10);
    expect(data.review_state).toBe('candidate');
    expect(data.check_relevance).toBe(true);
  });
  it('shows faster but worse outcomes rather than presenting omission as optimization', async () => {
    const original = request.getMockImplementation();
    request.mockImplementation(async (path, options) => path.includes('/compare?') ? {
      before_case_count: 1, after_case_count: 1, before_usage: run.usage, after_usage: run.usage,
      rows: [{ case_id: 'c1', key: 'Q01', before: 'passed', after: 'failed', before_seconds: 100, after_seconds: 30,
        change: 'regressed', timing_assessment: 'faster_with_quality_loss', points: { before: { covered: 10, total: 10 }, after: { covered: 4, total: 10 }, rows: [{ point_id: 'P5', before: 'covered', after: 'missing', change: 'regressed' }] } }],
    } : path.endsWith('/runs') ? [{ id: 'r1', name: '版本 A', created_at: 1 }, { id: 'r2', name: '旧基准', created_at: 1 }] : original(path, options));
    await open('/acceptance/a?run=r1');
    await wrapper.get('[aria-label="基准运行"]').setValue('r2');
    await button('与本次比较').trigger('click'); await flushPromises();
    expect(wrapper.text()).toContain('更快，但质量退化');
    expect(wrapper.text()).toContain('覆盖 10/10 → 4/10');
  });
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
    expect(button('第 1 轮 · 尝试 1')).toBeDefined();
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
    await open('/acceptance/a?run=r1'); await button('第 1 轮 · 尝试 1').trigger('click');
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
