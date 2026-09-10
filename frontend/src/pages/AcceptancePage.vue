<script setup>
import { computed, onUnmounted, ref, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { request, requestPage, command } from '../api.js';
import { useAuth } from '../stores/auth.js';
import { useWorkspace } from '../stores/workspace.js';
import MarkdownContent from '../components/MarkdownContent.vue';
import ErrorNotice from '../components/ErrorNotice.vue';
import AppDialog from '../components/AppDialog.vue';
import AcceptanceCaseEditor from '../components/AcceptanceCaseEditor.vue';
import AcceptanceSourcePicker from '../components/AcceptanceSourcePicker.vue';
import ParsingAcceptancePane from '../components/ParsingAcceptancePane.vue';
import AcceptanceLineage from '../components/AcceptanceLineage.vue';
import AcceptancePerformance from '../components/AcceptancePerformance.vue';
import '../styles/acceptance.css';

const route = useRoute(), router = useRouter(), auth = useAuth(), workspace = useWorkspace();
const spaces = computed(() => workspace.spaces.filter(s => auth.canManage(s.id)));
const space = computed(() => route.params.spaceId || '');
const base = computed(() => `/spaces/${space.value}/acceptance`);
const cases = ref([]), runs = ref([]), documents = ref([]), selected = ref([]), showArchived = ref(false);
const view = ref(route.query.view === 'parsing' ? 'parsing' : 'cases'), error = ref(null), busy = ref(false), loading = ref(false), message = ref('');
const editor = ref(null), revisions = ref([]), run = ref(null), attemptId = ref(''), diagnostics = ref(null);
const runName = ref(''), callLimit = ref(100), baseline = ref(''), comparison = ref(null);
const reviewMode = ref('assisted'), reviewEntries = ref([]), showGeneration = ref(false), generationPicking = ref(false), generationSources = ref([]), generationCount = ref(3), generationLimit = ref(3);
const reviewNote = ref(''), checked = ref([]), sourcesChecked = ref(false), importFile = ref(null);
let epoch = 0, detailEpoch = 0, timer, pendingCreate = null, pollRequest = null;
const labels = {
  ready: '待运行', running: '运行中', paused: '已暂停', completed: '已执行',
  pending_review: '待人工复核', passed: '通过', failed: '失败', incomplete: '未完成', not_run: '未运行',
  answered: '回答问题', insufficient_evidence: '资料不足时拒答', conflicting_evidence: '冲突时拒答',
  needs_clarification: '需要澄清', out_of_scope: '超出范围',
  improved: '改善', regressed: '退化', unchanged: '不变', not_comparable: '案例、资料、权限或评审方式已改变', pending: '待复核', removed: '本轮未选择',
};
const reasons = {
  ACCEPTANCE_MODEL_OUTPUT_TRUNCATED: '模型输出达到长度上限，本次未完成，已保留此前结果。可缩小选定原文范围后新建运行。',
  ACCEPTANCE_MODEL_INPUT_TOO_LARGE: '本批评审资料过长，请缩小要点引用的原文范围后新建运行。',
  POINT_REVIEW_WITNESS_INVALID: '评审结果中的引用无法对应原文或实际回答，已保留问答结果。可以继续以重试未完成的评审。',
  POINT_REVIEW_COUNT_INVALID: '模型返回的要点数量或编号不完整，可以继续未完成评审。',
  CANDIDATE_SOURCE_WITNESS_INVALID: '候选题引用了无法在选定原文中找到的文字，本次未导入案例。',
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
const lineageStages = { original: '原文件标准', parsed: '解析', chunks: '分块', retrieval: '检索', model_input: '实际生成输入', draft: '初次生成', final: '最终回答' };
const lineageLabels = { found: '原句已找到', not_found: '原句未找到', confirmed: '已核对', unassessed: '未独立核对', unrecorded: '未记录' };
const pointLabels = { covered: '覆盖', missing: '遗漏', incorrect: '错误', pending_review: '待复核' };
const timingLabels = { faster_with_quality_loss: '更快，但质量退化', faster_verified: '两侧均验收通过，耗时降低', quality_pending: '耗时降低，质量尚未确认', no_speedup: '未观察到提速' };
const pointSpecs = computed(() => [...(caseSpec.value?.criteria?.length ? caseSpec.value.criteria : caseSpec.value?.check_relevance ? [
  ...(caseSpec.value?.required_points || []).map((text, i) => ({ id: `required-${i + 1}`, text })),
  ...(caseSpec.value?.forbidden_claims || []).map((text, i) => ({ id: `forbidden-${i + 1}`, text })),
] : []), ...(caseSpec.value?.check_relevance ? [{ id: '__answer_relevance', kind: 'relevance', text: '回答相关性与重复附加内容', sources: [] }] : [])]);
const generatedCases = computed(() => (run.value?.attempts || []).flatMap(a => a.payload.generated_cases || []));
const when = stamp => new Date(stamp * 1000).toLocaleString('zh-CN', { hour12: false });
function selectSpace(event) { router.push(`/acceptance/${event.target.value}`); }
function selectAttempt(id) {
  attemptId.value = id; checked.value = []; sourcesChecked.value = false; reviewNote.value = ''; diagnostics.value = null;
  const rows = attempt.value?.point_results || attempt.value?.payload.point_results || [];
  reviewEntries.value = pointSpecs.value.map(p => { const r = rows.find(row => row.point_id === p.id); return { point_id: p.id, status: r?.status || 'pending_review', answer_quote: r?.answer_quote || '', note: r?.note || '' }; });
}
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
    history: [], expected_status: 'answered', required_points: [], forbidden_claims: [], check_relevance: true,
    required_source_documents: [], required_retrieved_documents: [], minimum_distinct_cited_documents: 0, source_notes: '', review_state: 'candidate', archived: false };
  c.required_retrieved_documents ||= [];
  editor.value = { id: row?.id, revision: row?.revision || 0, case: JSON.parse(JSON.stringify(c)),
    points: c.required_points.join('\n'), forbidden: c.forbidden_claims.join('\n'), history: JSON.stringify(c.history, null, 2) };
}
async function saveCase(data) {
  const e = editor.value;
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
  const body = { name: runName.value.trim() || `验收 ${new Date().toLocaleString('zh-CN')}`, case_ids: ids, call_limit: Number(callLimit.value), review_mode: reviewMode.value };
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
    verdict, note: reviewNote.value, checked_points: checked.value, sources_checked: sourcesChecked.value, point_reviews: reviewEntries.value,
  });
  if (result.review) { target.verdict = result.review.verdict; target.reviews.push(result.review); if (result.review.point_reviews) target.point_results = result.review.point_reviews; }
  else await openRun(run.value.id, false);
  message.value = '复核记录已保存。';
}
async function createGeneration() {
  const data = await command(`${base.value}/candidates:runs`, { sources: generationSources.value, count: generationCount.value, call_limit: generationLimit.value });
  showGeneration.value = false; await openRun(data.id); await load();
}
async function importGenerated() {
  const data = await command(`${base.value}/cases:import`, { cases: generatedCases.value });
  await load(); message.value = `已导入 ${data.imported.length} 道候选题。请逐项核对原文后确认。`;
}
async function compare() { comparison.value = await request(`${base.value}/compare?baseline=${baseline.value}&candidate=${run.value.id}`); }
watch(() => route.params.spaceId, async () => {
  ++epoch; clearInterval(timer); cases.value = []; runs.value = []; documents.value = []; selected.value = [];
  run.value = null; editor.value = null; comparison.value = null; generationSources.value = []; generationPicking.value = false; showGeneration.value = false; attemptId.value = ''; busy.value = false; error.value = null;
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
    <nav class="acceptance-tabs" aria-label="验收视图"><button :class="{ active: view === 'cases' }" @click="view = 'cases'">案例管理 <span>{{ cases.length }}</span></button><button :class="{ active: view === 'runs' }" @click="view = 'runs'">验收运行 <span>{{ runs.length }}</span></button><button :class="{ active: view === 'parsing' }" @click="view = 'parsing'">文档解析验收</button><button class="btn ghost" :disabled="busy || loading" @click="action(load)">刷新</button></nav>
    <template v-if="view === 'cases'">
      <div class="acceptance-toolbar"><div><button class="btn primary" :disabled="busy || loading" @click="edit()">新建案例</button><button class="btn" :disabled="busy || loading" @click="showGeneration = true">从文档生成候选题</button><button class="btn" :disabled="busy || loading" @click="importFile.click()">导入 JSON</button><input ref="importFile" type="file" accept=".json,application/json" hidden @change="importCases"><button class="btn" :disabled="!cases.length" @click="download('问答验收案例.json', { cases: cases.map(c => c.payload) })">导出案例</button></div><label><input v-model="showArchived" type="checkbox">显示归档案例</label></div>
      <section class="panel acceptance-table"><table><thead><tr><th>选择</th><th>案例 / 问题</th><th>范围</th><th>状态</th><th>操作</th></tr></thead><tbody><tr v-for="c in activeCases" :key="c.id"><td><input v-model="selected" :value="c.id" type="checkbox" :aria-label="`选择 ${c.payload.key}`" :disabled="c.payload.review_state !== 'confirmed' || c.payload.archived"></td><td><strong>{{ c.payload.key }}</strong><p>{{ c.payload.question }}</p><small>{{ c.payload.category }} · v{{ c.revision }}</small></td><td>{{ c.payload.reading.document_ids.length || '全库' }}{{ c.payload.reading.document_ids.length ? ' 份文件' : '' }}</td><td>{{ c.payload.archived ? '已归档' : c.payload.review_state === 'confirmed' ? '已确认' : '候选题' }}</td><td><button class="text-button" @click="edit(c)">编辑</button><button class="text-button" :disabled="busy" @click="action(() => archive(c))">{{ c.payload.archived ? '恢复' : '归档' }}</button></td></tr></tbody></table><p v-if="!activeCases.length" class="acceptance-empty">还没有案例。可以手动新建、导入已有案例，或在问答页面保存一条真实问题。</p></section>
      <section class="panel acceptance-run-form"><div><h2>创建验收运行</h2><p>已选择 {{ selected.length }} 道已确认案例。运行前保存案例、资料和配置版本。</p></div><label>运行名称<input v-model="runName" aria-label="运行名称" placeholder="例如：退款规则修复后"></label><label>评审方式<select v-model="reviewMode" aria-label="评审方式"><option value="assisted">程序检查 + 模型逐项建议 + 人工确认</option><option value="manual">程序检查 + 人工逐项复核</option></select></label><label>模型调用上限<input v-model.number="callLimit" aria-label="模型调用上限" type="number" min="1" max="2000"></label><button class="btn primary" :disabled="busy || !selected.length" @click="action(() => createRun())">创建运行</button></section>
    </template>
    <ParsingAcceptancePane v-else-if="view === 'parsing'" :key="space" :space="space" :documents="documents"/>
    <template v-else>
      <div class="acceptance-run-layout"><aside class="panel acceptance-run-list"><h2>运行记录</h2><button v-for="r in runs" :key="r.id" :class="{ active: run?.id === r.id }" @click="action(() => openRun(r.id))"><strong>{{ r.name }}</strong><small>{{ when(r.created_at) }} · {{ r.case_count }} 题</small><span>{{ r.queued ? '已排队' : labels[r.state] }} · {{ r.calls_reserved }} 次调用</span></button><p v-if="!runs.length">创建运行后，结果保存在这里。</p></aside>
      <section v-if="run" class="acceptance-run-detail"><div class="panel"><div class="acceptance-toolbar"><div><h2>{{ run.payload.name }}</h2><p>{{ run.queued ? '已排队' : labels[run.state] }} · {{ run.payload.snapshot.config.llm_model }} · {{ run.payload.kind === 'case_generation' ? '候选题生成' : run.payload.review_mode === 'assisted' ? '程序检查 + 模型建议 + 人工确认' : '程序检查 + 人工确认' }}</p></div><div><button v-if="['ready','paused'].includes(run.state)" class="btn primary" :disabled="busy || run.queued" @click="action(() => operate('resume'))">{{ run.queued ? '等待前序运行' : run.state === 'ready' ? '开始运行' : '继续未完成题' }}</button><button v-if="run.state === 'running'" class="btn" :disabled="busy" @click="action(() => operate('pause'))">暂停运行</button><button class="btn" @click="download(`验收-${run.id}.json`, run)">导出结果</button></div></div>
        <p v-if="run.reason" role="status" class="acceptance-notice">{{ pauseMessage }}</p>
        <div v-if="run.payload.kind !== 'case_generation'" class="acceptance-metrics"><div v-for="(n, state) in counts" :key="state"><strong>{{ n }}</strong><span>{{ labels[state] }}</span></div></div>
        <p class="small muted">已预留 / 发起 {{ run.calls_reserved }} / {{ run.call_limit }} 次调用；已收到 {{ run.usage.observed_calls }} 次调用记录。输入 {{ run.usage.input_tokens }} / 输出 {{ run.usage.output_tokens }} token，{{ run.usage.unknown_usage_calls }} 次用量未返回。{{ cost(run.usage) }}，{{ run.usage.unpriced_calls }} 次调用尚无可用计价。</p>
        <div v-if="['ready','paused'].includes(run.state)" class="acceptance-toolbar"><label>累计调用上限<input v-model.number="callLimit" type="number" aria-label="累计调用上限" min="1" max="2000"></label><button class="btn" :disabled="busy || callLimit <= run.call_limit" @click="action(async () => { await command(`${base}/runs/${run.id}:budget`, { call_limit: callLimit }); await openRun(run.id, false); })">增加预算</button></div>
        <details><summary>本次运行的资料与配置版本</summary><p>程序 {{ run.payload.snapshot.code_revision.slice(0, 12) }} · {{ run.payload.snapshot.evaluation_revision }}</p><ul><li v-for="s in run.payload.snapshot.sources" :key="s.document_id">{{ filename(s.document_id) }} · {{ s.visible ? '已发布' : '未发布' }} · {{ s.version_id || '无有效版本' }}</li></ul></details></div>
        <section v-if="run.payload.kind === 'case_generation'" class="panel"><h2>生成的候选题</h2><p>仅依据选定的 {{ run.payload.generation_sources?.length || 0 }} 段原文，不代表整份文档的覆盖率。</p><p v-for="c in generatedCases" :key="c.key">{{ c.question }} · {{ c.criteria.length }} 项要点</p><button class="btn primary" :disabled="busy || !generatedCases.length" @click="action(importGenerated)">导入为候选案例</button></section><section class="panel acceptance-table"><table><thead><tr><th>案例</th><th>最新结果</th><th>耗时</th><th>尝试</th></tr></thead><tbody><tr v-for="c in run.payload.cases" :key="c.id"><td><strong>{{ c.payload.key }}</strong><p>{{ c.payload.question }}</p></td><td>{{ labels[latest[c.id]?.verdict || 'not_run'] }}</td><td>问答 {{ latest[c.id]?.payload.qa_elapsed_seconds ?? '—' }} 秒<br><small>含验收 {{ latest[c.id]?.payload.elapsed_seconds ?? '—' }} 秒</small></td><td><button v-for="a in run.attempts.filter(a => a.case_id === c.id)" :key="a.id" class="text-button" @click="selectAttempt(a.id)">第 {{ a.attempt }} 次</button></td></tr></tbody></table></section>
        <div v-if="run.payload.kind !== 'case_generation'" class="acceptance-toolbar"><button class="btn" :disabled="busy" @click="action(() => createRun(run.payload.cases.map(c => c.id)))">用当前版本重新运行全部案例</button><button class="btn" :disabled="busy || !Object.values(latest).some(a => a.verdict === 'failed')" @click="action(() => createRun(Object.values(latest).filter(a => a.verdict === 'failed').map(a => a.case_id)))">新建失败题复测</button></div>
        <section v-if="run.payload.kind !== 'case_generation'" class="panel"><h2>比较两次运行</h2><div class="acceptance-toolbar"><label>基准运行<select v-model="baseline" aria-label="基准运行"><option value="">选择基准</option><option v-for="r in runs.filter(r => r.id !== run.id && r.kind !== 'case_generation')" :key="r.id" :value="r.id">{{ r.name }} · {{ when(r.created_at) }}</option></select></label><button class="btn" :disabled="busy || !baseline" @click="action(compare)">与本次比较</button></div><template v-if="comparison"><p>整轮 {{ comparison.before_case_count }} → {{ comparison.after_case_count }} 题，已记录调用 {{ comparison.before_usage.observed_calls }} → {{ comparison.after_usage.observed_calls }}；题数不同时，整轮用量不直接代表优化效果。</p><p v-if="comparison.common_before_usage">共同可比的 {{ comparison.common_case_count }} 题：包含重试与恢复前的调用，已记录 {{ comparison.common_before_usage.observed_calls }} → {{ comparison.common_after_usage.observed_calls }}；输入 token {{ comparison.common_before_usage.input_tokens }} → {{ comparison.common_after_usage.input_tokens }}；输出 token {{ comparison.common_before_usage.output_tokens }} → {{ comparison.common_after_usage.output_tokens }}；{{ cost(comparison.common_before_usage) }} → {{ cost(comparison.common_after_usage) }}。</p><p class="small muted">未完成或未复核的题目不计为改善；未收到记录的调用无法归属到单题，仍计入整轮未知用量。</p><table><thead><tr><th>案例</th><th>基准</th><th>本次</th><th>问答耗时变化</th><th>逐项质量</th><th>变化</th></tr></thead><tbody><tr v-for="r in comparison.rows" :key="r.case_id"><td>{{ r.key }}</td><td>{{ labels[r.before] }}</td><td>{{ labels[r.after] }}</td><td>{{ r.before_seconds ?? '—' }} → {{ r.after_seconds ?? '—' }} 秒</td><td><span v-if="r.points">覆盖 {{ r.points.before.covered }}/{{ r.points.before.total }} → {{ r.points.after.covered }}/{{ r.points.after.total }}</span><details v-if="r.points?.rows.length"><summary>要点变化</summary><p v-for="p in r.points.rows" :key="p.point_id">{{ p.text || p.point_id }}：{{ pointLabels[p.before] }} → {{ pointLabels[p.after] }}</p></details><details v-if="r.checks?.length"><summary>程序检查变化</summary><p v-for="check in r.checks" :key="check.name">{{ check.name }}：{{ check.before ? '通过' : '失败' }} → {{ check.after ? '通过' : '失败' }}</p></details><details v-if="r.lineage?.length"><summary>内容丢失阶段变化</summary><p v-for="entry in r.lineage" :key="entry.point_id + entry.stage">{{ entry.text }} · {{ lineageStages[entry.stage] }}：{{ lineageLabels[entry.before] }} → {{ lineageLabels[entry.after] }}（{{ labels[entry.change] }}）</p></details></td><td>{{ labels[r.change] }}<p>{{ timingLabels[r.timing_assessment] }}</p></td></tr></tbody></table></template></section>
      </section><section v-else class="panel acceptance-empty">选择一条运行记录查看结果。</section></div>
    </template>
  </template>

  <AcceptanceCaseEditor v-if="editor" :key="editor.id || 'new'" :editor="editor" :space="space" :documents="documents" :busy="busy" :error="error" @close="editor = null" @save="data => action(() => saveCase(data))"/>
  <AppDialog v-if="showGeneration" :open="true" title="从文档生成候选题" class="acceptance-dialog" @close="showGeneration = false"><p>先选择原文范围，系统生成问题、要点和依据。候选题需负责人确认后才能验收。</p><div v-for="(s, i) in generationSources" :key="s.chunk_id" class="source-binding"><strong>{{ filename(s.document_id) }}</strong><pre>{{ s.quote }}</pre><button class="text-button" @click="generationSources.splice(i, 1)">移除</button></div><button class="btn" :disabled="generationSources.length >= 20" @click="generationPicking = true">选择原文段落</button><label>候选题数量<input v-model.number="generationCount" type="number" min="1" max="10"></label><label>模型调用上限<input v-model.number="generationLimit" type="number" min="1" max="30"></label><p>创建后点击“开始运行”执行。生成记录支持暂停、恢复和预算增加。</p><button class="btn primary" :disabled="busy || !generationSources.length" @click="action(createGeneration)">创建生成任务</button><ErrorNotice v-if="error" :error="error"/></AppDialog>
  <AcceptanceSourcePicker v-if="generationPicking" :space="space" :documents="documents" @close="generationPicking = false" @select="source => { if (!generationSources.some(s => s.chunk_id === source.chunk_id)) generationSources.push(source); generationPicking = false; }"/>

  <AppDialog v-if="attempt" :open="true" :wide="true" class="acceptance-dialog" role="dialog" :title="`${caseSpec.key} · 第 ${attempt.attempt} 次 · 结果与证据`" @close="selectAttempt('')"><p>{{ caseSpec.question }}</p>
    <p v-if="attempt.payload.sources_unavailable" class="acceptance-notice">来源已失效，已隐藏原文与评审内容，请基于当前资料新建运行。</p><p v-if="attempt.payload.reused_qa_receipt" class="small muted">本次复用已完成的问答，只继续未完成的评审批次。</p><p v-if="attempt.payload.phase === 'semantic_review'" class="small muted">当前阶段：独立逐项评审，已完成的问答结果保留。</p><p>结果：{{ labels[attempt.verdict] }}。预期：{{ labels[caseSpec.expected_status] }}。</p><p v-if="attempt.payload.error_code" class="acceptance-notice">{{ reasons[attempt.payload.error_code] || attempt.payload.error_code }}</p>
    <ul class="acceptance-check-results"><li v-for="c in attempt.payload.checks" :key="c.name"><strong>{{ c.passed ? '✓' : '×' }} {{ c.name }}</strong><span>{{ c.detail }}</span></li></ul>
    <section v-for="(step, index) in attempt.payload.steps" :key="index" class="panel"><h3>{{ index < attempt.payload.steps.length - 1 ? '历史前置问题' : '本题执行' }}</h3><p>检索：{{ step.evidence.length }} 条证据 · 最终状态：{{ step.result.status }} · 核验：{{ step.result.verified ? '通过系统核验' : '未发布答案' }}</p><p v-if="step.failure?.code" class="acceptance-notice">失败阶段：{{ failureStages[step.failure.stage] || step.failure.stage || '待定位' }} · {{ step.failure.code }}<span v-if="step.failure.verification_stage"> · {{ verificationStages[step.failure.verification_stage] || step.failure.verification_stage }}</span><span v-if="step.failure.batch_number"> · 第 {{ step.failure.batch_number }} / {{ step.failure.batch_count }} 批，已完成 {{ step.failure.completed_batches }} 批</span><span v-if="step.failure.condition_id"> · 条件 {{ step.failure.condition_id }}</span><span v-if="step.failure.evidence_id"> · 证据 {{ step.failure.evidence_id }}</span><span v-if="step.failure.reason"> · {{ failureReasons[step.failure.reason] || step.failure.reason }}</span><span v-if="step.failure.conflicting_evidence_ids?.length"> · 冲突证据 {{ step.failure.conflicting_evidence_ids.join('、') }}</span><span v-if="step.failure.condition_ids?.length"> · 缺失条件 {{ step.failure.condition_ids.join('、') }}</span></p><AcceptancePerformance :summary="step.performance_summary"/><MarkdownContent v-if="step.result.answer" :text="step.result.answer"/><p v-else>没有发布答案正文。</p><details v-for="e in step.evidence" :key="e.evidence_id"><summary>{{ e.evidence_id }} · {{ filename(e.document_id) }} {{ step.result.citations?.some(c => c.evidence_id === e.evidence_id) ? '（答案已引用）' : '（检索证据）' }}</summary><p class="acceptance-source">{{ e.display_text || e.text }}</p><small>{{ JSON.stringify(e.locator) }}</small></details><details v-if="step.reading_progress && Object.keys(step.reading_progress).length"><summary>阅读覆盖记录</summary><pre>{{ JSON.stringify(step.reading_progress, null, 2) }}</pre></details><p class="small muted">运行编号 {{ step.result.rag_run_id || '未产生' }}</p></section>
    <AcceptanceLineage v-if="!attempt.payload.sources_unavailable" :report="attempt.payload.content_lineage"/>
    <section v-if="['completed','failed'].includes(attempt.state) && run.payload.kind !== 'case_generation' && !attempt.payload.sources_unavailable" class="panel"><h3>对照原文逐项复核</h3><p v-if="attempt.point_summary?.total">覆盖 {{ attempt.point_summary.covered }}/{{ attempt.point_summary.total }} · 遗漏 {{ attempt.point_summary.missing }} · 错误 {{ attempt.point_summary.incorrect }} · 待复核 {{ attempt.point_summary.pending_review }}</p>
      <div v-for="(point, i) in pointSpecs" :key="point.id" class="criterion-card"><h4>{{ i + 1 }}. {{ point.text }}</h4><details v-for="(s, j) in point.sources" :key="s.chunk_id + j"><summary>原文：{{ filename(s.document_id) }} · {{ s.version_id }}</summary><p class="acceptance-source">{{ s.quote }}</p></details><p v-if="attempt.payload.point_results?.[i]">模型建议：{{ pointLabels[attempt.payload.point_results[i].status] }} · {{ attempt.payload.point_results[i].note }}<span v-if="attempt.payload.point_results[i].source_quote"> · 原文见证：{{ attempt.payload.point_results[i].source_quote }}</span></p><template v-if="reviewEntries[i]"><label>复核结论<select v-model="reviewEntries[i].status"><option v-for="(label, status) in pointLabels" :key="status" :value="status">{{ label }}</option></select></label><label>对应回答原句<textarea v-model="reviewEntries[i].answer_quote" rows="2" placeholder="从实际回答复制；遗漏时留空。"/></label><label>判断依据<textarea v-model="reviewEntries[i].note" rows="2"/></label></template></div><label v-for="(point, index) in points" :key="index" class="acceptance-check"><input v-model="checked" type="checkbox" :value="index">{{ point }}</label><label class="acceptance-check"><input v-model="sourcesChecked" type="checkbox">我已核对引用原文及预期行为，检查是否遗漏限制或编造内容</label><label>复核说明<textarea v-model="reviewNote" rows="3" placeholder="说明事实、引用和限制的核对结果；失败时记录具体问题。"/></label><div class="acceptance-toolbar"><button class="btn primary" :disabled="busy || attempt.state === 'failed' || !sourcesChecked || (pointSpecs.length ? reviewEntries.some(p => p.status !== 'covered' || !p.note.trim()) : checked.length !== points.length) || !reviewNote.trim()" @click="action(() => review('passed'))">确认通过</button><button class="btn" :disabled="busy || !reviewNote.trim()" @click="action(() => review('failed'))">记录为失败</button></div><details v-for="r in attempt.reviews" :key="r.id"><summary>{{ when(r.created_at) }} · {{ labels[r.verdict] }} · 复核人 {{ r.actor_id }}</summary><p>{{ r.note }}</p><p v-for="p in r.point_reviews || []" :key="p.point_id">{{ p.point_id }} · {{ pointLabels[p.status] }} · {{ p.note }}<br>{{ p.answer_quote }}</p></details></section>
    <details v-if="auth.isSuper"><summary>管理员诊断</summary><p>包含未发布草稿和模型请求响应，仅供维护排查。</p><button class="btn" :disabled="busy" @click="action(async () => { diagnostics = await request(`${base}/runs/${run.id}/attempts/${attempt.id}/diagnostics`); })">读取详细诊断</button><pre v-if="diagnostics">{{ JSON.stringify(diagnostics, null, 2) }}</pre></details><ErrorNotice v-if="error" :error="error"/>
  </AppDialog>
</div></template>
