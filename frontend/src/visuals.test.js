import { beforeEach, afterEach, expect, it, vi } from 'vitest';
import { mount, flushPromises } from '@vue/test-utils';
import DocumentVisuals from './components/DocumentVisuals.vue';
import MermaidDiagram from './components/MermaidDiagram.vue';
import VisualAsset from './components/VisualAsset.vue';
import MarkdownContent from './components/MarkdownContent.vue';
import VisualEditor from './components/VisualEditor.vue';
import ConditionCoverage from './components/ConditionCoverage.vue';
import ReadingImageCoverage from './components/ReadingImageCoverage.vue';
import { normalizeTableCitations } from './markdownCitations';

const render = vi.hoisted(() => vi.fn());
vi.mock('./mermaid.js', () => ({ renderDiagram: render }));
let wrapper;
beforeEach(() => {
  vi.stubGlobal('fetch', vi.fn(async () => new Response(new Blob(['fixture-image']))));
  URL.createObjectURL = vi.fn(() => 'blob:private-fixture');
  URL.revokeObjectURL = vi.fn();
});
afterEach(() => { wrapper?.unmount(); vi.unstubAllGlobals(); vi.clearAllMocks(); sessionStorage.clear(); });
const json = data => new Response(JSON.stringify(data), { headers: { 'Content-Type': 'application/json' } });

it('retains trailing raw table citations without rewriting code blocks or unrelated links', () => {
  const table = '| 地区 | 费用 |\n|---|---|\n| 甲区 | 24元 | [E1] [E2]';
  wrapper = mount(MarkdownContent, { props: { text: table } });
  expect(wrapper.findAll('tbody td')).toHaveLength(2);
  expect(wrapper.findAll('tbody td')[1].text()).toContain('24元');
  expect(wrapper.findAll('tbody td')[1].text()).toContain('[E1] [E2]');
  for (const text of [
    `\`\`\`\`text\n~~~\n${table}\n~~~\n\`\`\`\``,
    table.split('\n').map(line => `    ${line}`).join('\n'),
    table.replace('[E1] [E2]', '[网页](https://example.com)'),
    '| 这只是普通正文 | [E1]',
  ]) expect(normalizeTableCitations(text)).toBe(text);
});

it('shows condition omissions without presenting a false complete badge or source excerpts', async () => {
  wrapper = mount(ConditionCoverage, { props:{ report:{ checked:3,covered:1,not_applicable:1,missing:1,complete:false,source_quote:'不可泄露旧版原文' } } });
  expect(wrapper.text()).toContain('1 项关键条件未完整保留');
  expect(wrapper.text()).toContain('答案未展示');
  expect(wrapper.text()).not.toContain('不可泄露旧版原文');
  await wrapper.setProps({report:{ checked:3,covered:2,not_applicable:1,missing:0,complete:true }});
  expect(wrapper.text()).toContain('2 项已保留');
  expect(wrapper.text()).toContain('1 项与本题无关');
  expect(wrapper.text()).not.toContain('未展示');
});
const asset = (id, title) => ({ id, status: 'verified', image_url: `/api/document-versions/${id}/visuals/image/image`, locator: { part: 'word/media/image1.png', paragraph: 3 }, extraction: { kind: 'table', title, description: '', transcription: '420 W' }, table_html: ['<table><tr><td rowspan="2">420 W</td><td><img src="x" onerror="alert(1)"><script>alert(1)</script>功率</td></tr></table>'] });

it('labels inherited unreviewed images as pending without presenting them as freshly recognized', async () => {
  const pending={...asset('pending','仍需复核'),status:'needs_review',stage:'needs_review',inherited_from_version_id:'base',issues:['原始疑点']};
  vi.stubGlobal('fetch',vi.fn(async()=>json({items:[pending],processing:{stage:'completed'}})));
  wrapper=mount(DocumentVisuals,{props:{versionId:'revised',documentId:'doc',filename:'doc.pdf'}});await flushPromises();
  expect(wrapper.get('.visual-inheritance-note').text()).toContain('沿用 1 张未改动图片');
  expect(wrapper.get('.visual-gallery').text()).toContain('沿用 · 需要复核');
  expect(wrapper.get('.visual-gallery').text()).not.toContain('识别中');
  expect(wrapper.get('.visual-status').text()).toBe('待复核');
});

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
  await flushPromises();
  expect(fetch).toHaveBeenCalledWith(expect.stringContaining('normalized=true'), expect.objectContaining({ credentials: 'include' }));
  expect(wrapper.get('img').attributes('src')).toBe('blob:private-fixture');
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

it('explains short review reasons and a text page incorrectly classified as a table before submission', async () => {
  const record = {...asset('readiness','文字页'), extraction:{kind:'table',title:'文字页',description:'',transcription:'原文内容',body_text:'',tables:[],graphs:[],uncertainties:[]}};
  wrapper = mount(VisualEditor, {props:{asset:record,issues:[{id:'1'.repeat(24),message:'原文需要核对',scope:'extraction'}]}});
  await wrapper.get('[aria-label="问题 1 处理结果"]').setValue('confirmed');
  await wrapper.get('[aria-label="问题 1 核对说明"]').setValue('1');
  await wrapper.get('[data-target="review-reason"] textarea').setValue('对照原图确认');
  await wrapper.get('input[type="checkbox"]').setValue(true);
  expect(wrapper.get('.review-progress').text()).toContain('0 项已完成');
  expect(wrapper.get('[aria-label="问题 1 核对说明"]').attributes('aria-invalid')).toBe('true');
  expect(wrapper.get('.review-issue').classes()).toContain('review-issue-invalid');
  expect(wrapper.get('.review-blockers').text()).toContain('当前 1 个');
  expect(wrapper.get('.review-blockers').text()).toContain('当前选择了“表格”');
  expect(wrapper.find('[aria-label="问题 1 关联对象"]').exists()).toBe(false);
  await wrapper.get('form').trigger('submit');
  expect(wrapper.emitted('save')).toBeUndefined();
  await wrapper.get('[data-target="kind"] select').setValue('text');
  await wrapper.get('[aria-label="问题 1 核对说明"]').setValue('对照原图第 1 行，转录一致');
  expect(wrapper.get('.review-progress').text()).toContain('1 项已完成');
  expect(wrapper.get('.review-issue').classes()).toContain('review-issue-resolved');
  expect(wrapper.find('.review-blockers').exists()).toBe(false);
  await wrapper.get('form').trigger('submit');
  expect(wrapper.emitted('save')[0][0].resolutions[0].disposition).toBe('confirmed');
  expect(wrapper.emitted('save')[0][0].extraction.transcription).toBe('原文内容');
});

it('does not mark a correction complete until the linked content actually changes', async () => {
  const record = {...asset('correction','文字页'), extraction:{kind:'text',title:'标题',description:'',transcription:'型号 Z12',body_text:'',tables:[],graphs:[],uncertainties:[]}};
  wrapper = mount(VisualEditor, {props:{asset:record,issues:[{id:'1'.repeat(24),message:'型号需要核对',scope:'extraction'}]}});
  await wrapper.get('[aria-label="问题 1 处理结果"]').setValue('corrected');
  await wrapper.get('[aria-label="问题 1 关联对象"]').setValue(['transcription']);
  await wrapper.get('[aria-label="问题 1 核对说明"]').setValue('原图型号为 Z13');
  expect(wrapper.get('.review-progress').text()).toContain('0 项已完成');
  expect(wrapper.get('[data-target="transcription"]').classes()).toContain('review-field-invalid');
  expect(wrapper.get('.review-blockers').text()).toContain('关联字段尚未实际修改');
  await wrapper.get('[data-target="title"] input').setValue('改了其他字段');
  expect(wrapper.get('.review-progress').text()).toContain('0 项已完成');
  await wrapper.get('[data-target="transcription"] textarea').setValue('型号 Z13');
  expect(wrapper.get('.review-progress').text()).toContain('1 项已完成');
  expect(wrapper.get('[data-target="transcription"]').classes()).toContain('review-field-resolved');
});

it('highlights only literal source excerpts and selects the corresponding editable text', async () => {
  const record = {...asset('excerpt','文字页'), extraction:{kind:'text',title:'型号',description:'',transcription:'设备型号 Z12，额定功率见表。',body_text:'',tables:[],graphs:[],uncertainties:[]}};
  wrapper = mount(VisualEditor, {attachTo:document.body,props:{asset:record,issues:[{id:'1'.repeat(24),message:'“Z12”需要对照原图确认；“不存在的文字”未出现在转录中。'}]}});
  expect(wrapper.findAll('.review-evidence mark').map(node=>node.text())).toEqual(['Z12']);
  await wrapper.get('.review-evidence button').trigger('click'); await flushPromises();
  const field = wrapper.get('[data-target="transcription"] textarea').element;
  expect(document.activeElement).toBe(field);
  expect(field.value.slice(field.selectionStart,field.selectionEnd)).toBe('Z12');
  const before=field.value;
  expect(wrapper.emitted('save')).toBeUndefined();
  expect(field.value).toBe(before);
});

it('edits graph topology and requires a separate disposition for every issue', async () => {
  const graph={direction:'LR',groups:[{id:'g',label:'生产区',parent:null,direction:'LR'}],nodes:[{id:'a',label:'A',group:'g',shape:'rectangle'},{id:'b',label:'B',group:null,shape:'diamond'}],edges:[{source:'a',target:'b',label:'失败',direction:'forward',style:'solid'}],uncertainties:['箭头不清楚']};
  const record={...asset('review','图'),extraction:{kind:'diagram',title:'图',description:'',transcription:'',body_text:'',tables:[],graphs:[graph],uncertainties:[]}};
  wrapper=mount(VisualEditor,{props:{asset:record,issues:[{id:'1'.repeat(24),message:'箭头不清楚'},{id:'2'.repeat(24),message:'分组需要核对'}]}});
  const find=text=>wrapper.findAll('button').find(b=>b.text()===text);
  await find('添加分组').trigger('click');
  await find('添加节点').trigger('click');
  await find('添加连线').trigger('click');
  expect(wrapper.findAll('.graph-item')).toHaveLength(7);
  await wrapper.get('[aria-label="图 1 分组 g1"]').setValue('测试区');
  await wrapper.get('[aria-label="问题 1 处理结果"]').setValue('corrected');
  await wrapper.get('[aria-label="问题 1 关联对象"]').setValue(['graphs/0']);
  await wrapper.get('[aria-label="问题 1 核对说明"]').setValue('对照原图补回遗漏对象与连接');
  await wrapper.findAll('textarea').at(-1).setValue('修复遗漏结构');
  await wrapper.get('input[type="checkbox"]').setValue(true);
  expect(find('确认修订并生成新版本').attributes('disabled')).toBeDefined();
  await wrapper.get('[aria-label="问题 2 处理结果"]').setValue('confirmed');
  await wrapper.get('[aria-label="问题 2 核对说明"]').setValue('原图中分组边界与现有记录一致');
  await wrapper.get('form').trigger('submit');
  const sent=wrapper.emitted('save')[0][0];
  expect(sent.resolutions).toHaveLength(2);
  expect(sent.extraction.graphs[0].groups).toHaveLength(2);
  expect(sent.extraction.graphs[0].nodes).toHaveLength(3);
  expect(sent.extraction.graphs[0].edges).toHaveLength(2);
  expect(sent.extraction.graphs[0].uncertainties).toEqual([]);
  expect(wrapper.text()).not.toContain('我已逐项修正并确认这些问题');
});

it('keeps the document condition captured at the start of editing', async () => {
  const record={...asset('locked','图'),table_html:[],extraction:{kind:'text',title:'图',description:'原文',transcription:'',body_text:'',tables:[],graphs:[],uncertainties:[]}};
  const sent=[];
  vi.stubGlobal('fetch',vi.fn((url,options={})=>{
    if(String(url).endsWith('/visuals')) return Promise.resolve(json({items:[record]}));
    if(String(url).endsWith('/visual-review-context')) return Promise.resolve(json({row_version:7,historical:false,issues:{locked:[]}}));
    if(options.method==='POST') {sent.push(options);return Promise.resolve(json({document_version_id:'new'}));}
    throw Error('Unexpected latest-state refresh would discard the editing condition: '+url);
  }));
  wrapper=mount(DocumentVisuals,{props:{versionId:'v1',documentId:'doc',filename:'file.md'}}); await flushPromises();
  await wrapper.findAll('button').find(b=>b.text()==='修订这张图').trigger('click'); await flushPromises();
  const editor=wrapper.getComponent(VisualEditor);
  await editor.findAll('textarea')[0].setValue('修订内容');
  await editor.findAll('textarea').at(-1).setValue('对照原文修订');
  await editor.get('input[type="checkbox"]').setValue(true);
  await editor.get('form').trigger('submit'); await flushPromises();
  expect(sent[0].headers.get('If-Match')).toBe('7');
});

it('does not show guessed model boxes as verified original-image locations', async () => {
  const record={...asset('bbox','图'),table_html:[],extraction:{kind:'diagram',description:'',graphs:[{groups:[],nodes:[{id:'n',label:'组件',bbox:[.1,.2,.3,.4],bbox_basis:'unverified'}],edges:[]}]}};
  wrapper=mount(VisualAsset,{props:{asset:record}});
  await wrapper.findAll('button').find(b=>b.text()==='定位 组件').trigger('click');
  expect(wrapper.find('.visual-region').exists()).toBe(false);
  await wrapper.setProps({asset:{...record,extraction:{...record.extraction,graphs:[{groups:[],nodes:[{...record.extraction.graphs[0].nodes[0],bbox_basis:'human'}],edges:[]}]}}});
  await wrapper.findAll('button').find(b=>b.text()==='定位 组件').trigger('click');
  expect(wrapper.get('.visual-region').attributes('style')).toContain('left: 10%');
  expect(wrapper.text()).toContain('人工对照原图确认');
});

it('preserves a complete table grid across insertion, deletion, merging and splitting', async () => {
  const {resizeTable,mergeCells,splitCell}=await import('./visualEditing.js');
  const table={rows:2,columns:2,header_rows:1,cells:[0,1,2,3].map(i=>({row:Math.floor(i/2),column:i%2,rowspan:1,colspan:1,text:String(i)}))};
  expect(mergeCells(table,0,0,1,2)).toBe(true);
  resizeTable(table,'column',1);
  expect(table.cells.find(c=>c.row===0&&c.column===0).colspan).toBe(3);
  resizeTable(table,'row',0);
  expect(table.header_rows).toBe(2);
  const merged=table.cells.find(c=>c.colspan===3);
  splitCell(table,merged);
  resizeTable(table,'column',1,true);
  resizeTable(table,'row',0,true);
  expect(table.rows).toBe(2);expect(table.columns).toBe(2);
  const seen=new Set();for(const c of table.cells) for(let r=c.row;r<c.row+c.rowspan;r++) for(let k=c.column;k<c.column+c.colspan;k++) {expect(seen.has(`${r}:${k}`)).toBe(false);seen.add(`${r}:${k}`);}
  expect(seen.size).toBe(4);
});

it('invalidates review target bindings when graph object positions change', async () => {
  const record={...asset('binding','图'),table_html:[],extraction:{kind:'diagram',title:'',description:'',transcription:'',body_text:'',tables:[],graphs:[{direction:'LR',groups:[],nodes:[{id:'a',label:'A',group:null,shape:'rectangle'},{id:'b',label:'B',group:null,shape:'rectangle'}],edges:[],uncertainties:[]}],uncertainties:[]}};
  wrapper=mount(VisualEditor,{props:{asset:record,issues:[{id:'1'.repeat(24),message:'A 应为 C'}]}});
  await wrapper.get('[aria-label="问题 1 处理结果"]').setValue('corrected');
  await wrapper.get('[aria-label="问题 1 关联对象"]').setValue(['graphs/0/nodes/1']);
  await wrapper.findAll('button').find(b=>b.text()==='删除误识别节点').trigger('click');
  expect(Array.from(wrapper.get('[aria-label="问题 1 关联对象"]').element.selectedOptions)).toHaveLength(0);
  expect(wrapper.text()).toContain('请重新关联各问题的对象');
  expect(wrapper.get('.region-editor select').element.value).toBe('');
});

it('labels partial graphs and candidate citation targets without claiming full approval', async () => {
  const record={...asset('partial','流程'),table_html:[],graph_coverage:[{partial:true}],focus_targets:[{fact_id:'f1',text:'A 指向 B',usage:'citation_candidate',bbox:[.1,.2,.3,.4],bbox_basis:'human'}],extraction:{kind:'diagram',description:'',graphs:[{groups:[],nodes:[],edges:[]}]}};
  wrapper=mount(VisualAsset,{props:{asset:record}});
  expect(wrapper.text()).toContain('部分内容可用于问答');
  expect(wrapper.text()).not.toContain('原图核对通过');
  expect(wrapper.text()).toContain('该条引用包含的关系（点击定位）');
  expect(wrapper.find('.visual-region').exists()).toBe(false);
  await wrapper.findAll('button').find(b=>b.text()==='A 指向 B').trigger('click');
  expect(wrapper.find('.visual-region').exists()).toBe(true);
});

it('distinguishes answer-used graph facts from historical citation candidates', async () => {
  const record={...asset('facts','流程'),table_html:[],focus_targets:[{fact_id:'used',text:'已使用的关系',usage:'answer',bbox:[.1,.2,.3,.4],bbox_basis:'human'}],extraction:{kind:'diagram',description:'',graphs:[]}};
  wrapper=mount(VisualAsset,{props:{asset:record}});
  expect(wrapper.text()).toContain('回答引用的事实位置');
  expect(wrapper.text()).not.toContain('该条引用包含的关系（点击定位）');
  expect(wrapper.find('.visual-region').exists()).toBe(false);
  await wrapper.findAll('button').find(b=>b.text()==='已使用的关系').trigger('click');
  expect(wrapper.text()).toContain('人工对照原图确认');
});

it('distinguishes confirmed-image reuse from the image recheck budget count', async () => {
  wrapper=mount(ReadingImageCoverage,{props:{report:{checked_images:0,image_checks:[{status:'supported',fact_ids:['fact']}]}}});
  expect(wrapper.get('.reading-image-summary').text()).toBe('已使用已确认的图片资料');
  expect(wrapper.get('.reading-image-summary').text()).not.toContain('0');
  expect(wrapper.get('details').attributes('open')).toBeUndefined();
  expect(wrapper.get('details').text()).toContain('进入图像复查预算：0 张');
  expect(wrapper.get('details').text()).toContain('不代表本轮重新调用模型看图');
  await wrapper.setProps({report:{checked_images:2,image_checks:[{status:'supported'}]}});
  expect(wrapper.get('.reading-image-summary').text()).toBe('图像复查 2 张（可复用已确认结果）');
  await wrapper.setProps({report:{checked_images:0,image_checks:[]}});
  expect(wrapper.find('.reading-image-coverage').exists()).toBe(false);
});
