import { mount } from '@vue/test-utils';
import { describe, expect, it } from 'vitest';
import AnswerPerformance from './components/AnswerPerformance.vue';

describe('问答耗时', () => {
  it('shows queue wait and batch failures without summing overlapping stages', () => {
    const wrapper = mount(AnswerPerformance, { props: { report: { elapsed_seconds: 12.5, events: [
      { kind: 'model_http', sent: true, queue_seconds: 2 },
      { kind: 'model_http', sent: false, queue_seconds: 3 },
      { kind: 'stage', name: 'verification.conditions', batch_number: 2, seconds: 8, status: 'failed' },
      { kind: 'stage', name: 'rag.ask.claim.reverify', seconds: 9, status: 'error' },
    ] } } });
    expect(wrapper.text()).toContain('处理耗时 12.50 秒');
    expect(wrapper.text()).toContain('模型请求 1 次 · 累计额度排队 5.00 秒');
    expect(wrapper.text()).toMatch(/条件批次\s*2：8\.00 秒（未完成）/);
    expect(wrapper.text()).toContain('阶段耗时不能直接相加');
    expect(wrapper.text()).toContain('补全后重新核验：9.00 秒（未完成）');
  });
  it('labels exact reuse and current permission checks', () => {
    const wrapper = mount(AnswerPerformance, { props: { report: { elapsed_seconds: 0.8, events: [
      { kind: 'cache', cache: 'verified_result', outcome: 'hit' },
    ] } } });
    expect(wrapper.text()).toContain('已复用验证结果');
    expect(wrapper.text()).toContain('返回前已重新检查当前权限与发布状态');
    expect(wrapper.text()).toContain('模型请求 0 次');
  });
});
