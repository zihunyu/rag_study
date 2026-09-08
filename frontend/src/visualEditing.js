export const clone = value => JSON.parse(JSON.stringify(value));
export const uniqueId = (graph, prefix) => { const ids = new Set([...graph.nodes, ...graph.groups].map(x => x.id)); let i = 1; while (ids.has(`${prefix}${i}`)) i++; return `${prefix}${i}`; };
export function excludeGraphItem(graph, kind, index) {
  const item = graph[kind][index]; item.review_status = 'excluded';
  const removed = new Set([...graph.nodes, ...graph.groups].filter(x=>x.review_status==='excluded').map(x=>x.id));
  let changed = true;
  while (changed) { changed = false; for (const group of graph.groups) if (removed.has(group.parent) && !removed.has(group.id)) { removed.add(group.id); group.review_status='excluded'; changed=true; } }
  for (const node of graph.nodes) if (removed.has(node.group)) { node.review_status='excluded'; removed.add(node.id); }
  for (const edge of graph.edges) if (removed.has(edge.source) || removed.has(edge.target)) edge.review_status='excluded';
}
export function deleteGraphItem(graph, kind, index) {
  const item = graph[kind][index];
  if (kind === 'groups') { for (const group of graph.groups) if (group.parent===item.id) group.parent=item.parent; for (const node of graph.nodes) if (node.group===item.id) node.group=item.parent; }
  if (kind !== 'edges') graph.edges = graph.edges.filter(e=>e.source!==item.id && e.target!==item.id);
  graph[kind].splice(index, 1);
}
export function resizeTable(table, axis, index, remove = false) {
  const dim = axis==='row' ? 'rows' : 'columns', span = axis==='row' ? 'rowspan' : 'colspan', other = axis==='row' ? 'column' : 'row';
  if (remove && table[dim]===1) return;
  if (!remove && (table.rows * table.columns >= 10000 || table[dim]>=1000)) return;
  const cells=[];
  for(const source of table.cells) {
    const cell={...source}, start=cell[axis], end=start+cell[span];
    if(remove) { if(start>index) cell[axis]--; else if(start<=index && end>index) { if(cell[span]===1) continue; cell[span]--; } }
    else { if(start>=index) cell[axis]++; else if(end>index) cell[span]++; }
    cells.push(cell);
  }
  table[dim] += remove ? -1 : 1;
  if(!remove) for(let i=0;i<table[axis==='row'?'columns':'rows'];i++) {
    if(!cells.some(c=>c[axis]<=index && c[axis]+c[span]>index && c[other]<=i && c[other]+c[axis==='row'?'colspan':'rowspan']>i)) cells.push({row:axis==='row'?index:i,column:axis==='row'?i:index,rowspan:1,colspan:1,text:''});
  }
  table.cells=cells.sort((a,b)=>a.row-b.row || a.column-b.column);
  if(axis==='row') { if(remove && index<table.header_rows) table.header_rows--; else if(!remove && index<table.header_rows) table.header_rows++; }
}
export function splitCell(table, cell) {
  table.cells=table.cells.filter(c=>c!==cell);
  for(let r=cell.row;r<cell.row+cell.rowspan;r++) for(let c=cell.column;c<cell.column+cell.colspan;c++) table.cells.push({row:r,column:c,rowspan:1,colspan:1,text:r===cell.row&&c===cell.column?cell.text:''});
}
export function mergeCells(table, row, column, rowspan, colspan) {
  if(![row,column,rowspan,colspan].every(Number.isInteger)) return false;
  if(row<0 || column<0 || rowspan<1 || colspan<1 || row+rowspan>table.rows || column+colspan>table.columns) return false;
  const overlapping=table.cells.filter(c=>c.row<row+rowspan && c.row+c.rowspan>row && c.column<column+colspan && c.column+c.colspan>column);
  if(overlapping.some(c=>c.row<row || c.column<column || c.row+c.rowspan>row+rowspan || c.column+c.colspan>column+colspan)) return false;
  table.cells=table.cells.filter(c=>!overlapping.includes(c));
  table.cells.push({row,column,rowspan,colspan,text:overlapping.sort((a,b)=>a.row-b.row||a.column-b.column).map(c=>c.text).filter(Boolean).join('\n')});
  return true;
}
export function draftProblems(draft) {
  if (!draft) return [];
  const problems = [];
  const add = (target, message) => problems.push({ target, message, invalid: true });
  if (draft.kind === 'unknown') add('kind', '图片类型尚未明确，请先选择原图实际内容类型。');
  for (const [gi, graph] of (draft.graphs || []).entries()) {
    const target = `graphs/${gi}`;
    if (!graph.nodes.length) add(target, '关系图至少需要一个原图可见节点；空图请删除整个关系图。');
    const ids = [...graph.nodes, ...graph.groups].map(n => n.id), groups = new Map(graph.groups.map(g => [g.id, g]));
    if (ids.some(id => !id.trim()) || new Set(ids).size !== ids.length) add(target, '节点与分组标识重复。');
    for (const group of graph.groups) {
      let id = group.id; const seen = new Set();
      while (id !== null) {
        if (!groups.has(id) || seen.has(id)) { add(target, '分组层级不能循环，父分组必须存在。'); break; }
        seen.add(id); id = groups.get(id).parent;
      }
    }
    if (graph.nodes.some(n => n.group !== null && !groups.has(n.group)) || graph.edges.some(e => !ids.includes(e.source) || !ids.includes(e.target))) add(target, '节点归属或连线端点不存在。');
    if ([...graph.nodes, ...graph.groups, ...graph.edges].some(x => x.review_status === 'pending')) add(target, '仍有待核对的关系对象，请核实或明确排除。');
    const excluded = new Set([...graph.nodes, ...graph.groups].filter(x => x.review_status === 'excluded').map(x => x.id));
    if (graph.edges.some(e => (excluded.has(e.source) || excluded.has(e.target)) && e.review_status !== 'excluded')) add(target, '已排除对象的关联连线必须一并排除。');
  }
  if (draft.kind === 'diagram' && !draft.graphs.length) add('kind', '当前选择了“关系图 / 流程图”，但没有关系图结构。请核对图片类型或补充原图中实际存在的结构。');
  if (draft.kind === 'table' && !draft.tables.length) add('kind', '当前选择了“表格”，但没有完整表格。纯文字页面请选择“文字图片”；原图确有表格时请补充表格结构。');
  if (draft.graphs.length && !['diagram', 'mixed'].includes(draft.kind)) add('kind', '包含关系图时请选择关系图或混合内容。');
  return problems;
}
export const validateDraft = draft => draftProblems(draft)[0]?.message || '';
