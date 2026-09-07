import { afterEach, expect, it, vi } from 'vitest';
import { mount, flushPromises } from '@vue/test-utils';
import DocumentVisuals from './components/DocumentVisuals.vue';
import MermaidDiagram from './components/MermaidDiagram.vue';
import VisualAsset from './components/VisualAsset.vue';
import MarkdownContent from './components/MarkdownContent.vue';
import VisualEditor from './components/VisualEditor.vue';

const render = vi.hoisted(() => vi.fn());
vi.mock('./mermaid.js', () => ({ renderDiagram: render }));
let wrapper;
afterEach(() => { wrapper?.unmount(); vi.unstubAllGlobals(); vi.clearAllMocks(); sessionStorage.clear(); });
const json = data => new Response(JSON.stringify(data), { headers: { 'Content-Type': 'application/json' } });
const asset = (id, title) => ({ id, status: 'verified', image_url: `/api/document-versions/${id}/visuals/image/image`, locator: { part: 'word/media/image1.png', paragraph: 3 }, extraction: { kind: 'table', title, description: '', transcription: '420 W' }, table_html: ['<table><tr><td rowspan="2">420 W</td><td><img src="x" onerror="alert(1)"><script>alert(1)</script>功率</td></tr></table>'] });

it('does not display a late image manifest from the previous document version', async () => {
  let finishFirst;
  vi.stubGlobal('fetch', vi.fn(url => String(url).includes('/v1/')
    ? new Promise(resolve => { finishFirst = resolve; })
    : Promise.resolve(json({ items: [asset('v2', '当前版本的表格')] }))));
  wrapper = mount(DocumentVisuals, { props: { versionId: 'v1', documentId: 'doc', filename: 'doc.docx' } });
  await wrapper.setProps({ versionId: 'v2' }); await flushPromises();
  finishFirst(json({ items: [asset('v1', '旧版本的图')] })); await flushPromises();
  expect(wrapper.text()).toContain('当前版本的表格');
  expect(wrapper.text()).not.toContain('旧版本的图');
});

it('preserves table cells and source positions while sanitizing extracted markup', () => {
  wrapper = mount(VisualAsset, { props: { asset: asset('v1', '参数表') } });
  expect(wrapper.get('td').attributes('rowspan')).toBe('2');
  expect(wrapper.text()).toContain('420 W');
  expect(wrapper.text()).toContain('3');
  expect(wrapper.find('.visual-table img').exists()).toBe(false);
  expect(wrapper.find('script').exists()).toBe(false);
});

it('labels unverified interpretations as unusable evidence', () => {
  wrapper = mount(VisualAsset, { props: { asset: { ...asset('v1', '模糊表格'), status: 'needs_review', issues: ['第三行数值模糊'] } } });
  expect(wrapper.text()).toContain('尚不能作为问答依据');
  expect(wrapper.text()).toContain('第三行数值模糊');
  expect(wrapper.text()).not.toContain('原图核对通过');
});

it('renders source tables without showing HTML code or allowing active content', () => {
  wrapper = mount(MarkdownContent, { props: { sourceTables: true, text: '原文表格\n\n<table><tr><td rowspan="2">420 W</td><td><script>alert(1)</script><img src="x">功率</td></tr></table>\n\n```html\n<table><tr><td>代码样例</td></tr></table>\n```' } });
  expect(wrapper.findAll('table')).toHaveLength(1);
  expect(wrapper.get('td').text()).toBe('420 W');
  expect(wrapper.get('td').attributes('rowspan')).toBe('2');
  expect(wrapper.get('pre').text()).toContain('<table>');
  expect(wrapper.find('script').exists()).toBe(false);
  expect(wrapper.find('img').exists()).toBe(false);
});

it('cancels late Mermaid rendering and permits ordinary words in labels', async () => {
  let finishFirst;
  render.mockImplementationOnce(() => new Promise(resolve => { finishFirst = resolve; }))
    .mockResolvedValueOnce('<svg><text>当前结构</text><script>alert(1)</script></svg>');
  wrapper = mount(MermaidDiagram, { props: { code: 'flowchart LR\n n1["click here"]' } });
  await flushPromises();
  await wrapper.setProps({ code: 'flowchart LR\n n1["当前结构"]' }); await flushPromises();
  finishFirst('<svg><text>旧结构</text></svg>'); await flushPromises();
  expect(wrapper.get('svg').text()).toBe('当前结构');
  expect(wrapper.find('script').exists()).toBe(false);
  expect(render).toHaveBeenCalledTimes(2);
});

it('rejects Mermaid interaction directives before rendering', async () => {
  wrapper = mount(MermaidDiagram, { props: { code: 'flowchart LR\n n1["节点"]\n click n1 "https://example.com"' } });
  await flushPromises();
  expect(render).not.toHaveBeenCalled();
  expect(wrapper.text()).toContain('请查看原图');
});

it('retains image zoom and result tab after polling and browser refresh', async () => {
  wrapper = mount(VisualAsset, { props:{ asset:asset('stable','表格'), viewKey:'version:stable' } });
  await wrapper.get('[aria-label="放大原图"]').trigger('click');
  await wrapper.findAll('button').find(b=>b.text()==='提取文字').trigger('click');
  await wrapper.setProps({ asset:{ ...asset('stable','表格'), updated_at:2 } });
  expect(wrapper.text()).toContain('125%');
  wrapper.unmount();
  wrapper = mount(VisualAsset, { props:{ asset:asset('stable','表格'), viewKey:'version:stable' } });
  await flushPromises();
  expect(wrapper.text()).toContain('125%');
  expect(wrapper.get('.visual-tabs .active').text()).toBe('提取文字');
});

it('shows only measured OCR boxes and leaves ambiguous cells unlocated', async () => {
  const record = { ...asset('locate','参数'), coordinate_space:'normalized_exif_white_background',
    table_html:['<table><tr><td>323 W</td></tr></table>'],
    extraction:{kind:'table',tables:[{cells:[{row:0,column:0,text:'323 W'}]}]},
    regions:[{id:'r1',text:'323 W',bbox:[.1,.2,.3,.4],score:.99}],
    local_check:{targets:[{kind:'cell',table:0,row:0,column:0,region_ids:['r1']}]}};
  wrapper = mount(VisualAsset, { props:{ asset:record } });
  await wrapper.get('td').trigger('click');
  expect(wrapper.get('.visual-region').attributes('style')).toContain('left: 10%');
  expect(wrapper.get('img').attributes('src')).toContain('normalized=true');
  await wrapper.setProps({asset:{...record, local_check:{targets:[]}}});
  await wrapper.get('td').trigger('click');
  expect(wrapper.find('.visual-region').exists()).toBe(false);
  expect(wrapper.text()).toContain('暂不能唯一定位');
});

it('does not erase a human edit when the same asset is refreshed', async () => {
  const record = {...asset('editor','图'), extraction:{kind:'text',description:'原文字',body_text:'',tables:[],graphs:[],uncertainties:[]}};
  wrapper = mount(VisualEditor, {props:{asset:record}});
  await wrapper.findAll('textarea')[0].setValue('已核对的说明');
  await wrapper.setProps({asset:{...record,updated_at:3}});
  expect(wrapper.findAll('textarea')[0].element.value).toBe('已核对的说明');
  expect(wrapper.emitted('save')).toBeUndefined();
  await wrapper.findAll('textarea').at(-1).setValue('对照原图纠正');
  await wrapper.get('input[type="checkbox"]').setValue(true);
  await wrapper.get('form').trigger('submit');
  expect(wrapper.emitted('save')[0][0].extraction.description).toBe('已核对的说明');
});
