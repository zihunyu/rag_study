import { mount } from '@vue/test-utils';
import { describe, expect, it } from 'vitest';
import AcceptanceRepeats from './components/AcceptanceRepeats.vue';

describe('重复运行的质量与耗时', () => {
  it('keeps model suggestions separate from human approval and shows worst latency', () => {
    const wrapper = mount(AcceptanceRepeats, { props: { summary: {
      planned_samples: 3,
      groups: [{ group: 'answered', count: 3, median_seconds: 20, worst_seconds: 90 }],
      rows: [{ case_id: 'c', key: 'Q01', completed: 3, planned: 3, mechanical_passed: 3,
        assisted_passed: 3, human_passed: 0, median_seconds: 20, worst_seconds: 90 }],
    } } });
    expect(wrapper.text()).toContain('20.00 秒');
    expect(wrapper.text()).toContain('90.00 秒');
    expect(wrapper.text()).toContain('0/3');
    expect(wrapper.text()).toContain('“模型建议通过”不能替代人工确认');
    wrapper.unmount();
  });
  it('does not label an insufficient-evidence shortcut as successful speedup', () => {
    const wrapper = mount(AcceptanceRepeats, { props: { comparison: {
      snapshot_differences: ['code_revision'],
      rows: [{ case_id: 'c', key: 'Q01', before: { median_seconds: 20, worst_seconds: 90 },
        after: { median_seconds: 1, worst_seconds: 2 }, assessment: 'outcome_changed',
        median_change_percent: null }],
    } } });
    expect(wrapper.text()).toContain('回答状态改变，不能计为提速');
    expect(wrapper.text()).toContain('不可比');
    expect(wrapper.text()).toContain('code_revision');
    wrapper.unmount();
  });
});
