<script setup>
import { computed, onUnmounted, ref, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { request, requestPage, command } from '../api.js';
import { useAuth } from '../stores/auth.js';
import { useWorkspace } from '../stores/workspace.js';
import MarkdownContent from '../components/MarkdownContent.vue';
import ErrorNotice from '../components/ErrorNotice.vue';
import AppDialog from '../components/AppDialog.vue';
import '../styles/acceptance.css';

const route = useRoute(), router = useRouter(), auth = useAuth(), workspace = useWorkspace();
const spaces = computed(() => workspace.spaces.filter(s => auth.canManage(s.id)));
const space = computed(() => route.params.spaceId || '');
const base = computed(() => `/spaces/${space.value}/acceptance`);
const cases = ref([]), runs = ref([]), documents = ref([]), selected = ref([]), showArchived = ref(false);
const view = ref('cases'), error = ref(null), busy = ref(false), loading = ref(false), message = ref('');
const editor = ref(null), revisions = ref([]), run = ref(null), attemptId = ref(''), diagnostics = ref(null);
const runName = ref(''), callLimit = ref(100), baseline = ref(''), comparison = ref(null);
const reviewNote = ref(''), checked = ref([]), sourcesChecked = ref(false), importFile = ref(null);
let epoch = 0, detailEpoch = 0, timer, pendingCreate = null, pollRequest = null;
const labels = {
  ready: '待运行', running: '运行中', paused: '已暂停', completed: '已执行',
  pending_review: '待人工复核', passed: '通过', failed: '失败', incomplete: '未完成', not_run: '未运行',
  answered: '回答问题', insufficient_evidence: '资料不足时拒答', conflicting_evidence: '冲突时拒答',
  needs_clarification: '需要澄清', out_of_scope: '超出范围',
  improved: '改善', regressed: '退化', unchanged: '不变', not_comparable: '案例、资料或权限已改变', pending: '待复核', removed: '本轮未选择',
};
const reasons = {
  CALL_BUDGET_EXHAUSTED: '调用预算已用完。可增加上限后继续。',
  WORKER_INTERRUPTED: '执行进程中断，已保存完成题目，可继续未完成题。',
  RUN_PAUSED: '运行已暂停，当前未完成的题目会在继续时重新执行。',
  MODEL_SERVICE_UNAVAILABLE: '模型服务暂不可用，恢复后可继续。',
  MODEL_PROVIDER_RATE_LIMITED: '上游模型服务触发限流（HTTP 429），已暂停并保留结果；恢复后可继续。',
  REQUEST_DEADLINE_EXCEEDED: '本题处理超过总时限，已保存收到的结果与诊断，可查看失败尝试后继续。',
  ProviderTimeout: '本题处理超时，可查看已记录的模型调用后继续。',
  VERIFIER_TIMEOUT: '答案核验超时，未发布答案；可查看核验阶段和批次后继续。',
  RUN_SNAPSHOT_CHANGED_CREATE_NEW_RUN: '资料或配置已改变，请新建运行，保留原记录用于比较。',
  HISTORY_PREREQUISITE_FAILED: '历史前置问题未成功，尚未执行目标问题。',
};
const activeCases = computed(() => cases.value.filter(c => showArchived.value || !c.payload.archived));
const latest = computed(() => Object.fromEntries((run.value?.attempts || []).map(a => [a.case_id, a])));
const attempt = computed(() => run.value?.attempts.find(a => a.id === attemptId.value));
const pauseMessage = computed(() => {
  let reason = run.value?.reason;
  const result = run.value?.attempts?.at(-1)?.payload.steps?.at(-1)?.result;
  const lastCall = result?.coverage_report?.performance?.events?.filter(e => e.kind === 'model_http').at(-1);
  if (reason === 'MODEL_SERVICE_UNAVAILABLE' && (result?.warnings?.includes('MODEL_PROVIDER_RATE_LIMITED') || lastCall?.outcome === '429')) reason = 'MODEL_PROVIDER_RATE_LIMITED';
  return reasons[reason] || reason;
});
const caseSpec = computed(() => run.value?.payload.cases.find(c => c.id === attempt.value?.case_id)?.payload);
const points = computed(() => [...(caseSpec.value?.required_points || []).map(t => `必须满足：${t}`),
  ...(caseSpec.value?.forbidden_claims || []).map(t => `不能出现：${t}`)]);
const counts = computed(() => {
  const count = { passed: 0, failed: 0, pending_review: 0, incomplete: 0 };
  for (const c of run.value?.payload.cases || []) {
    const verdict = latest.value[c.id]?.verdict || 'incomplete';
    count[verdict in count ? verdict : 'incomplete']++;
  }
  return count;
});
const cost = usage => !usage?.observed_calls ? '尚无记录' : usage.unpriced_calls >= usage.observed_calls ? '费用未知' : `已知费用 ¥${Number(usage.known_cost_cny || 0).toFixed(4)}（${usage.unpriced_calls} 次未知）`;
const failureStages = { retrieval: '检索', generation: '生成', verification: '核验', reading: '阅读' };
const verificationStages = { conditions: '条件分批核验', claims_and_conflicts: '回答、引用与完整证据冲突核验', condition_repair: '补全遗漏条件' };
const failureReasons = { applicability_reason_mismatch: '适用性结论与解释自相矛盾', condition_repair_sources_exceed_budget: '补全所需资料超过预算', condition_batch_limit_exceeded: '待核验条件超过本轮批次数量上限', single_condition_too_large: '单条原文条件超过处理容量', condition_source_not_cited: '核验器将未引用的来源判为已覆盖', quote_not_in_answer: '核验引用的句子不在实际回答中', quote_does_not_address_condition: '引用的回答没有覆盖该条件' };
const filename = id => documents.value.find(d => d.document_id === id)?.filename || id;
const lines = value => value.split('\n').map(v => v.trim()).filter(Boolean);
const when = stamp => new Date(stamp * 1000).toLocaleString('zh-CN', { hour12: false });
function selectSpace(event) { router.push(`/acceptance/${event.target.value}`); }
function selectAttempt(id) { attemptId.value = id; checked.value = []; sourcesChecked.value = false; reviewNote.value = ''; diagnostics.value = null; }
async function action(fn) {
  if (busy.value) return;
  const own = epoch;
  busy.value = true; error.value = null; message.value = '';
  try { await fn(); } catch (cause) { if (own === epoch) error.value = cause; }
  finally { if (own === epoch) busy.value = false; }
}
async function load() {
  if (!space.value) return;
  const own = epoch, path = base.value;
  loading.value = true;
  try {
    const [nextCases, nextRuns] = await Promise.all([request(`${path}/cases`), request(`${path}/runs`)]);
    if (own !== epoch) return;
    cases.value = nextCases; runs.value = nextRuns;
  } finally { if (own === epoch) loading.value = false; }
}
async function loadDocuments() {
  const own = epoch, id = space.value;
  let cursor = '', rows = [];
  do {
    const page = await requestPage(`/spaces/${id}/documents/preview?limit=100${cursor ? `&cursor=${encodeURIComponent(cursor)}` : ''}`);
    rows.push(...page.items); cursor = page.nextCursor;
  } while (cursor && own === epoch);
  if (own === epoch) documents.value = rows;
}
async function openRun(id, navigate = true) {
  const own = epoch, detailRequest = ++detailEpoch;
  const data = await request(`${base.value}/runs/${id}`);
  if (own !== epoch || detailRequest !== detailEpoch) return;
  if (run.value?.id !== id) { comparison.value = null; baseline.value = ''; selectAttempt(''); }
  run.value = data; view.value = 'runs'; callLimit.value = data.call_limit;
  const listRow = runs.value.find(r => r.id === id);
  if (listRow) Object.assign(listRow, { state: data.state, calls_reserved: data.calls_reserved, queued: data.queued });
  if (navigate) router.replace({ query: { run: id } });
}
function edit(row = null) {
  revisions.value = [];
  const c = row?.payload || { key: '', category: '业务问答', question: '', reading: { mode: 'fact', document_ids: [] },
    history: [], expected_status: 'answered', required_points: [], forbidden_claims: [],
    required_source_documents: [], required_retrieved_documents: [], minimum_distinct_cited_documents: 0, source_notes: '', review_state: 'candidate', archived: false };
  c.required_retrieved_documents ||= [];
  editor.value = { id: row?.id, revision: row?.revision || 0, case: JSON.parse(JSON.stringify(c)),
    points: c.required_points.join('\n'), forbidden: c.forbidden_claims.join('\n'), history: JSON.stringify(c.history, null, 2) };
}
async function saveCase() {
  const e = editor.value;
  const data = { ...e.case, required_points: lines(e.points), forbidden_claims: lines(e.forbidden), history: JSON.parse(e.history || '[]') };
  await request(`${base.value}/cases${e.id ? `/${e.id}` : ''}`, { method: e.id ? 'PUT' : 'POST', body: JSON.stringify({ case: data, revision: e.revision }) });
  editor.value = null; await load(); message.value = '案例已保存，历史版本仍可查看。';
}
async function archive(row) {
  await request(`${base.value}/cases/${row.id}`, { method: 'PUT', body: JSON.stringify({ case: { ...row.payload, archived: !row.payload.archived }, revision: row.revision }) });
  selected.value = selected.value.filter(id => id !== row.id); await load();
}
function download(name, data) {
  const url = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' }));
  const a = document.createElement('a'); a.href = url; a.download = name; a.click(); URL.revokeObjectURL(url);
}
async function importCases(event) {
  const file = event.target.files[0]; if (!file) return;
  await action(async () => {
    if (file.size > 2_000_000) throw new Error('导入文件不能超过 2 MB');
    const data = JSON.parse(await file.text());
    const input = Array.isArray(data) ? data : data.cases;
    if (!Array.isArray(input)) throw new Error('请选择本功能导出的案例 JSON 文件');
    const result = await command(`${base.value}/cases:import`, { cases: input.map(c => c.payload || c) });
    await load(); message.value = `导入 ${result.imported.length} 道候选题，跳过 ${result.skipped_existing.length} 道已有题。请核对依据后确认。`;
  });
  event.target.value = '';
}
async function createRun(ids = selected.value) {
  if (!ids.length) throw new Error('请选择已确认的案例');
  const body = { name: runName.value.trim() || `验收 ${new Date().toLocaleString('zh-CN')}`, case_ids: ids, call_limit: Number(callLimit.value) };
  const payloadKey = JSON.stringify(body);
  if (!pendingCreate || pendingCreate.payloadKey !== payloadKey) pendingCreate = { key: crypto.randomUUID(), payloadKey };
  const data = await command(`${base.value}/runs`, body, pendingCreate.key);
  pendingCreate = null;
  await openRun(data.id); await load();
}
async function operate(verb) {
  await command(`${base.value}/runs/${run.value.id}:${verb}`);
  await openRun(run.value.id, false); await load();
}
async function review(verdict) {
  ++detailEpoch;
  const target = attempt.value;
  const result = await command(`${base.value}/runs/${run.value.id}/attempts/${target.id}/reviews`, {
    verdict, note: reviewNote.value, checked_points: checked.value, sources_checked: sourcesChecked.value,
  });
  if (result.review) { target.verdict = result.review.verdict; target.reviews.push(result.review); }
  else await openRun(run.value.id, false);
  message.value = '复核记录已保存。';
}
async function compare() { comparison.value = await request(`${base.value}/compare?baseline=${baseline.value}&candidate=${run.value.id}`); }
watch(() => route.params.spaceId, async () => {
  ++epoch; clearInterval(timer); cases.value = []; runs.value = []; documents.value = []; selected.value = [];
  run.value = null; editor.value = null; comparison.value = null; attemptId.value = ''; busy.value = false; error.value = null;
  if (!space.value) return;
  await action(async () => {
    await Promise.all([load(), loadDocuments()]);
    if (route.query.run) await openRun(route.query.run, false);
    if (route.query.conversation && route.query.turn) {
      const key = `会话-${String(route.query.turn).slice(-12)}`;
      let candidate = cases.value.find(c => c.payload.key === key);
      if (!candidate) {
        candidate = await command(`${base.value}/cases:from-turn`, { key, conversation_id: route.query.conversation, turn_id: route.query.turn });
        await load();
      }
      edit(candidate); router.replace({ query: {} });
    }
  });
  timer = setInterval(() => {
    if (!busy.value && !pollRequest && run.value && ['ready', 'running'].includes(run.value.state)) {
      const own = epoch;
      const pending = openRun(run.value.id, false);
      pollRequest = pending;
      pending.catch(cause => { if (own === epoch) { error.value = cause; clearInterval(timer); } })
        .finally(() => { if (pollRequest === pending) pollRequest = null; });
    }
  }, 3000);
}, { immediate: true });
onUnmounted(() => { ++epoch; clearInterval(timer); });
</script>

<template><div class="page acceptance-page">
  <div class="page-heading"><div><p class="eyebrow">ANSWER ACCEPTANCE</p><h1>问答验收</h1><p class="page-description">用同一组问题检查知识质量，追踪每一次改善与退化。</p></div>
    <label class="acceptance-space">知识库<select aria-label="验收知识库" :value="space" @change="selectSpace"><option value="" disabled>选择负责的知识库</option><option v-for="s in spaces" :key="s.id" :value="s.id">{{ s.name }}</option></select></label></div>
  <ErrorNotice v-if="error" :error="error"/><p v-if="message" role="status" class="acceptance-notice">{{ message }}</p>
  <section v-if="!space" class="panel acceptance-empty"><h2>从一个知识库开始</h2><p>选择知识库，建立验收案例。案例单独保存，业务文件照常使用。</p></section>
  <template v-else>
    <nav class="acceptance-tabs" aria-label="验收视图"><button :class="{ active: view === 'cases' }" @click="view = 'cases'">案例管理 <span>{{ cases.length }}</span></button><button :class="{ active: view === 'runs' }" @click="view = 'runs'">验收运行 <span>{{ runs.length }}</span></button><button class="btn ghost" :disabled="busy || loading" @click="action(load)">刷新</button></nav>
    <template v-if="view === 'cases'">
      <div class="acceptance-toolbar"><div><button class="btn primary" :disabled="busy || loading" @click="edit()">新建案例</button><button class="btn" :disabled="busy || loading" @click="importFile.click()">导入 JSON</button><input ref="importFile" type="file" accept=".json,application/json" hidden @change="importCases"><button class="btn" :disabled="!cases.length" @click="download('问答验收案例.json', { cases: cases.map(c => c.payload) })">导出案例</button></div><label><input v-model="showArchived" type="checkbox">显示归档案例</label></div>
      <section class="panel acceptance-table"><table><thead><tr><th>选择</th><th>案例 / 问题</th><th>范围</th><th>状态</th><th>操作</th></tr></thead><tbody><tr v-for="c in activeCases" :key="c.id"><td><input v-model="selected" :value="c.id" type="checkbox" :aria-label="`选择 ${c.payload.key}`" :disabled="c.payload.review_state !== 'confirmed' || c.payload.archived"></td><td><strong>{{ c.payload.key }}</strong><p>{{ c.payload.question }}</p><small>{{ c.payload.category }} · v{{ c.revision }}</small></td><td>{{ c.payload.reading.document_ids.length || '全库' }}{{ c.payload.reading.document_ids.length ? ' 份文件' : '' }}</td><td>{{ c.payload.archived ? '已归档' : c.payload.review_state === 'confirmed' ? '已确认' : '候选题' }}</td><td><button class="text-button" @click="edit(c)">编辑</button><button class="text-button" :disabled="busy" @click="action(() => archive(c))">{{ c.payload.archived ? '恢复' : '归档' }}</button></td></tr></tbody></table><p v-if="!activeCases.length" class="acceptance-empty">还没有案例。可以手动新建、导入已有案例，或在问答页面保存一条真实问题。</p></section>
      <section class="panel acceptance-run-form"><div><h2>创建验收运行</h2><p>已选择 {{ selected.length }} 道已确认案例。运行前保存案例、资料和配置版本。</p></div><label>运行名称<input v-model="runName" aria-label="运行名称" placeholder="例如：退款规则修复后"></label><label>模型调用上限<input v-model.number="callLimit" aria-label="模型调用上限" type="number" min="1" max="2000"></label><button class="btn primary" :disabled="busy || !selected.length" @click="action(() => createRun())">创建运行</button></section>
    </template>
    <template v-else>
      <div class="acceptance-run-layout"><aside class="panel acceptance-run-list"><h2>运行记录</h2><button v-for="r in runs" :key="r.id" :class="{ active: run?.id === r.id }" @click="action(() => openRun(r.id))"><strong>{{ r.name }}</strong><small>{{ when(r.created_at) }} · {{ r.case_count }} 题</small><span>{{ r.queued ? '已排队' : labels[r.state] }} · {{ r.calls_reserved }} 次调用</span></button><p v-if="!runs.length">创建运行后，结果保存在这里。</p></aside>
      <section v-if="run" class="acceptance-run-detail"><div class="panel"><div class="acceptance-toolbar"><div><h2>{{ run.payload.name }}</h2><p>{{ run.queued ? '已排队' : labels[run.state] }} · {{ run.payload.snapshot.config.llm_model }} · 复核方式：程序检查 + 人工确认</p></div><div><button v-if="['ready','paused'].includes(run.state)" class="btn primary" :disabled="busy || run.queued" @click="action(() => operate('resume'))">{{ run.queued ? '等待前序运行' : run.state === 'ready' ? '开始运行' : '继续未完成题' }}</button><button v-if="run.state === 'running'" class="btn" :disabled="busy" @click="action(() => operate('pause'))">暂停运行</button><button class="btn" @click="download(`验收-${run.id}.json`, run)">导出结果</button></div></div>
        <p v-if="run.reason" role="status" class="acceptance-notice">{{ pauseMessage }}</p>
        <div class="acceptance-metrics"><div v-for="(n, state) in counts" :key="state"><strong>{{ n }}</strong><span>{{ labels[state] }}</span></div></div>
        <p class="small muted">已预留 / 发起 {{ run.calls_reserved }} / {{ run.call_limit }} 次调用；已收到 {{ run.usage.observed_calls }} 次调用记录。输入 {{ run.usage.input_tokens }} / 输出 {{ run.usage.output_tokens }} token，{{ run.usage.unknown_usage_calls }} 次用量未返回。{{ cost(run.usage) }}，{{ run.usage.unpriced_calls }} 次调用尚无可用计价。</p>
        <div v-if="['ready','paused'].includes(run.state)" class="acceptance-toolbar"><label>累计调用上限<input v-model.number="callLimit" type="number" aria-label="累计调用上限" min="1" max="2000"></label><button class="btn" :disabled="busy || callLimit <= run.call_limit" @click="action(async () => { await command(`${base}/runs/${run.id}:budget`, { call_limit: callLimit }); await openRun(run.id, false); })">增加预算</button></div>
        <details><summary>本次运行的资料与配置版本</summary><p>程序 {{ run.payload.snapshot.code_revision.slice(0, 12) }} · {{ run.payload.snapshot.evaluation_revision }}</p><ul><li v-for="s in run.payload.snapshot.sources" :key="s.document_id">{{ filename(s.document_id) }} · {{ s.visible ? '已发布' : '未发布' }} · {{ s.version_id || '无有效版本' }}</li></ul></details></div>
        <section class="panel acceptance-table"><table><thead><tr><th>案例</th><th>最新结果</th><th>耗时</th><th>尝试</th></tr></thead><tbody><tr v-for="c in run.payload.cases" :key="c.id"><td><strong>{{ c.payload.key }}</strong><p>{{ c.payload.question }}</p></td><td>{{ labels[latest[c.id]?.verdict || 'not_run'] }}</td><td>{{ latest[c.id]?.payload.elapsed_seconds ?? '—' }} 秒</td><td><button v-for="a in run.attempts.filter(a => a.case_id === c.id)" :key="a.id" class="text-button" @click="selectAttempt(a.id)">第 {{ a.attempt }} 次</button></td></tr></tbody></table></section>
        <div class="acceptance-toolbar"><button class="btn" :disabled="busy" @click="action(() => createRun(run.payload.cases.map(c => c.id)))">用当前版本重新运行全部案例</button><button class="btn" :disabled="busy || !Object.values(latest).some(a => a.verdict === 'failed')" @click="action(() => createRun(Object.values(latest).filter(a => a.verdict === 'failed').map(a => a.case_id)))">新建失败题复测</button></div>
        <section class="panel"><h2>比较两次运行</h2><div class="acceptance-toolbar"><label>基准运行<select v-model="baseline" aria-label="基准运行"><option value="">选择基准</option><option v-for="r in runs.filter(r => r.id !== run.id)" :key="r.id" :value="r.id">{{ r.name }} · {{ when(r.created_at) }}</option></select></label><button class="btn" :disabled="busy || !baseline" @click="action(compare)">与本次比较</button></div><template v-if="comparison"><p>整轮 {{ comparison.before_case_count }} → {{ comparison.after_case_count }} 题，已记录调用 {{ comparison.before_usage.observed_calls }} → {{ comparison.after_usage.observed_calls }}；题数不同时，整轮用量不直接代表优化效果。</p><p v-if="comparison.common_before_usage">共同可比的 {{ comparison.common_case_count }} 题：最新完整尝试已记录调用 {{ comparison.common_before_usage.observed_calls }} → {{ comparison.common_after_usage.observed_calls }}；输入 token {{ comparison.common_before_usage.input_tokens }} → {{ comparison.common_after_usage.input_tokens }}；输出 token {{ comparison.common_before_usage.output_tokens }} → {{ comparison.common_after_usage.output_tokens }}；{{ cost(comparison.common_before_usage) }} → {{ cost(comparison.common_after_usage) }}。</p><p class="small muted">未完成或未复核的题目不计为改善；未收到记录的调用无法归属到单题，仍计入整轮未知用量。</p><table><thead><tr><th>案例</th><th>基准</th><th>本次</th><th>耗时变化</th><th>变化</th></tr></thead><tbody><tr v-for="r in comparison.rows" :key="r.case_id"><td>{{ r.key }}</td><td>{{ labels[r.before] }}</td><td>{{ labels[r.after] }}</td><td>{{ r.before_seconds ?? '—' }} → {{ r.after_seconds ?? '—' }} 秒</td><td>{{ labels[r.change] }}</td></tr></tbody></table></template></section>
      </section><section v-else class="panel acceptance-empty">选择一条运行记录查看结果。</section></div>
    </template>
  </template>

  <AppDialog v-if="editor" :open="true" class="acceptance-dialog" role="dialog" :title="editor.id ? '编辑验收案例' : '新建验收案例'" @close="editor = null"><form @submit.prevent="action(saveCase)"><div class="acceptance-fields"><label>案例编号<input v-model="editor.case.key" :disabled="!!editor.id" required maxlength="80"></label><label>分类<input v-model="editor.case.category" maxlength="100"></label></div><label>问题<textarea v-model="editor.case.question" required maxlength="4000" rows="3"/></label><label>预期行为<select v-model="editor.case.expected_status"><option v-for="s in ['answered','insufficient_evidence','conflicting_evidence','needs_clarification','out_of_scope']" :key="s" :value="s">{{ labels[s] }}</option></select></label><label>必须满足的事实和限制（每行一项）<textarea v-model="editor.points" rows="4" placeholder="例如：三年，从购买凭证记载的购买日期起算"/></label><label>不能出现的说法（每行一项）<textarea v-model="editor.forbidden" rows="3"/></label>
    <fieldset><legend>本题可以读取的文件（不选表示当前知识库）</legend><label v-for="d in documents" :key="d.document_id" class="acceptance-check"><input v-model="editor.case.reading.document_ids" type="checkbox" :value="d.document_id">{{ d.filename }}</label></fieldset><fieldset><legend>答案必须引用的文件</legend><label v-for="d in documents" :key="d.document_id" class="acceptance-check"><input v-model="editor.case.required_source_documents" type="checkbox" :value="d.document_id">{{ d.filename }}</label></fieldset>
    <div class="acceptance-fields"><label>阅读方式<select v-model="editor.case.reading.mode"><option value="auto">自动判断</option><option value="fact">查找具体问题</option><option value="overview">逐章总结</option><option value="compare">跨文件综合</option></select></label><label>至少引用几份文件<input v-model.number="editor.case.minimum_distinct_cited_documents" type="number" min="0" max="20"></label></div>
    <label>原文依据与说明<textarea v-model="editor.case.source_notes" rows="4" placeholder="填写关键原文、文件和位置；资料不足或冲突案例说明拒答依据。"/></label><details><summary>检索要求、多轮问题与版本历史</summary><fieldset><legend>检索必须包含的文件（适用于冲突等案例）</legend><label v-for="d in documents" :key="d.document_id" class="acceptance-check"><input v-model="editor.case.required_retrieved_documents" type="checkbox" :value="d.document_id">{{ d.filename }}</label></fieldset><p class="small muted">前置问题按顺序真实执行，格式为 question 与 reading；不会把参考答案送入问答。</p><textarea v-model="editor.history" aria-label="历史前置问题 JSON" rows="6"/><button v-if="editor.id" class="btn" type="button" @click="action(async () => { revisions = await request(`${base}/cases/${editor.id}/revisions`); })">查看历史版本</button><details v-for="r in revisions" :key="r.revision"><summary>v{{ r.revision }} · {{ when(r.created_at) }}</summary><pre>{{ JSON.stringify(r.payload, null, 2) }}</pre></details></details>
    <label class="acceptance-check"><input :checked="editor.case.review_state === 'confirmed'" type="checkbox" @change="editor.case.review_state = $event.target.checked ? 'confirmed' : 'candidate'">我已核对原文和预期要求，确认该案例可以运行</label><ErrorNotice v-if="error" :error="error"/><footer><button class="btn" type="button" @click="editor = null">取消</button><button class="btn primary" :disabled="busy" type="submit">保存案例</button></footer></form></AppDialog>

  <AppDialog v-if="attempt" :open="true" :wide="true" class="acceptance-dialog" role="dialog" :title="`${caseSpec.key} · 第 ${attempt.attempt} 次 · 结果与证据`" @close="selectAttempt('')"><p>{{ caseSpec.question }}</p>
    <p>结果：{{ labels[attempt.verdict] }}。预期：{{ labels[caseSpec.expected_status] }}。</p><p v-if="attempt.payload.error_code" class="acceptance-notice">{{ reasons[attempt.payload.error_code] || attempt.payload.error_code }}</p>
    <ul class="acceptance-check-results"><li v-for="c in attempt.payload.checks" :key="c.name"><strong>{{ c.passed ? '✓' : '×' }} {{ c.name }}</strong><span>{{ c.detail }}</span></li></ul>
    <section v-for="(step, index) in attempt.payload.steps" :key="index" class="panel"><h3>{{ index < attempt.payload.steps.length - 1 ? '历史前置问题' : '本题执行' }}</h3><p>检索：{{ step.evidence.length }} 条证据 · 最终状态：{{ step.result.status }} · 核验：{{ step.result.verified ? '通过系统核验' : '未发布答案' }}</p><p v-if="step.failure?.code" class="acceptance-notice">失败阶段：{{ failureStages[step.failure.stage] || step.failure.stage || '待定位' }} · {{ step.failure.code }}<span v-if="step.failure.verification_stage"> · {{ verificationStages[step.failure.verification_stage] || step.failure.verification_stage }}</span><span v-if="step.failure.batch_number"> · 第 {{ step.failure.batch_number }} / {{ step.failure.batch_count }} 批，已完成 {{ step.failure.completed_batches }} 批</span><span v-if="step.failure.condition_id"> · 条件 {{ step.failure.condition_id }}</span><span v-if="step.failure.evidence_id"> · 证据 {{ step.failure.evidence_id }}</span><span v-if="step.failure.reason"> · {{ failureReasons[step.failure.reason] || step.failure.reason }}</span><span v-if="step.failure.conflicting_evidence_ids?.length"> · 冲突证据 {{ step.failure.conflicting_evidence_ids.join('、') }}</span><span v-if="step.failure.condition_ids?.length"> · 缺失条件 {{ step.failure.condition_ids.join('、') }}</span></p><MarkdownContent v-if="step.result.answer" :text="step.result.answer"/><p v-else>没有发布答案正文。</p><details v-for="e in step.evidence" :key="e.evidence_id"><summary>{{ e.evidence_id }} · {{ filename(e.document_id) }} {{ step.result.citations?.some(c => c.evidence_id === e.evidence_id) ? '（答案已引用）' : '（检索证据）' }}</summary><p class="acceptance-source">{{ e.display_text || e.text }}</p><small>{{ JSON.stringify(e.locator) }}</small></details><details v-if="step.reading_progress && Object.keys(step.reading_progress).length"><summary>阅读覆盖记录</summary><pre>{{ JSON.stringify(step.reading_progress, null, 2) }}</pre></details><p class="small muted">运行编号 {{ step.result.rag_run_id || '未产生' }}</p></section>
    <section v-if="['completed','failed'].includes(attempt.state)" class="panel"><h3>对照原文复核</h3><label v-for="(point, index) in points" :key="index" class="acceptance-check"><input v-model="checked" type="checkbox" :value="index">{{ point }}</label><label class="acceptance-check"><input v-model="sourcesChecked" type="checkbox">我已核对引用原文及预期行为，检查是否遗漏限制或编造内容</label><label>复核说明<textarea v-model="reviewNote" rows="3" placeholder="说明事实、引用和限制的核对结果；失败时记录具体问题。"/></label><div class="acceptance-toolbar"><button class="btn primary" :disabled="busy || attempt.state === 'failed' || !sourcesChecked || checked.length !== points.length || !reviewNote.trim()" @click="action(() => review('passed'))">确认通过</button><button class="btn" :disabled="busy || !reviewNote.trim()" @click="action(() => review('failed'))">记录为失败</button></div><p v-for="r in attempt.reviews" :key="r.id" class="small">{{ when(r.created_at) }} · {{ labels[r.verdict] }} · {{ r.note }}</p></section>
    <details v-if="auth.isSuper"><summary>管理员诊断</summary><p>包含未发布草稿和模型请求响应，仅供维护排查。</p><button class="btn" :disabled="busy" @click="action(async () => { diagnostics = await request(`${base}/runs/${run.id}/attempts/${attempt.id}/diagnostics`); })">读取详细诊断</button><pre v-if="diagnostics">{{ JSON.stringify(diagnostics, null, 2) }}</pre></details><ErrorNotice v-if="error" :error="error"/>
  </AppDialog>
</div></template>
