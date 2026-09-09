<script setup>
import { useAuth } from '../stores/auth.js';
const auth = useAuth();
import { sessionFetch } from '../authTransport.js';
import { computed, ref, watch, nextTick, onUnmounted } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { Plus, MessagesSquare, Send, Square, Sparkles, ArrowUpRight, FileText, Copy, ThumbsUp, ThumbsDown, X, PanelLeft, Pencil, Archive, Check, BookOpen, LoaderCircle, ChevronDown } from '@lucide/vue';
import { useConversations } from '../stores/conversations.js';
import { useWorkspace } from '../stores/workspace.js';
import { request, requestPage, sourceUrl } from '../api.js';
import { locationLabel, dateTime, visualEvidenceNotice } from '../format.js';
import { useResource } from '../composables/useResource.js';
import ReadingImageCoverage from '../components/ReadingImageCoverage.vue';
import MarkdownContent from '../components/MarkdownContent.vue';
import VisualAsset from '../components/VisualAsset.vue';
import ErrorNotice from '../components/ErrorNotice.vue';
import AppDialog from '../components/AppDialog.vue';
import ConditionCoverage from '../components/ConditionCoverage.vue';
import AnswerPerformance from '../components/AnswerPerformance.vue';
const route = useRoute(), router = useRouter(), store = useConversations(), workspace = useWorkspace();
const spaceId = ref(route.query.space || workspace.selectedId || ''), question = ref(''), historyOpen = ref(false), source = ref(null), copied = ref(''), feedback = ref({}), actionError = ref(null), renameOpen = ref(false), title = ref(''), renameBusy = ref(false), creating = ref(false);
const bodyElement = ref(null);
const readingMode = ref(route.query.mode || 'auto'), documentScope = ref(route.query.document || ''), readableDocuments = ref([]), documentCursor = ref(null), documentsBusy = ref(false);
let documentRevision = 0;
async function loadReadable(more = false) { const own = ++documentRevision, space = spaceId.value; if (!space || !auth.canManage(space)) return; documentsBusy.value = true;
 try { const page = await requestPage(`/spaces/${space}/documents?limit=100${more && documentCursor.value ? '&cursor='+encodeURIComponent(documentCursor.value) : ''}`); if (own === documentRevision && spaceId.value === space) { readableDocuments.value = more ? [...readableDocuments.value, ...page.items] : page.items; documentCursor.value = page.nextCursor; } } catch { /* The question endpoint still enforces scope and publication. */ } finally { if (own === documentRevision) documentsBusy.value = false; }
}
watch(spaceId, () => { readableDocuments.value = []; documentCursor.value = null; documentScope.value = route.query.document || ''; loadReadable(); }, { immediate:true });
const readingOptions = () => ({ mode:readingMode.value, document_ids: auth.canManage(spaceId.value) && documentScope.value ? [documentScope.value] : [] });
const currentSpace = computed(() => workspace.spaces.find(space => space.id === spaceId.value));
const sourceResource = useResource(async signal => { const response = await sessionFetch(sourceUrl(source.value.source_url), { signal }); if (!response.ok) throw new Error('SOURCE_REFERENCE_NOT_FOUND'); return response.json(); });
watch(() => route.params.conversationId, async id => { source.value = null; sourceResource.clear(); historyOpen.value = false; question.value = ''; actionError.value = null; await store.activate(id); if (store.current) { spaceId.value = store.current.space_id; workspace.select(spaceId.value); const reading = store.turns.at(-1)?.reading || {}; readingMode.value = reading.mode || route.query.mode || 'auto'; documentScope.value = reading.document_ids?.[0] || route.query.document || ''; } store.list(spaceId.value); await scrollBottom(); }, { immediate: true });
watch(() => workspace.spaces, async spaces => {
  if (workspace.loading) return;
  if (!spaces.some(s => s.id === spaceId.value)) {
    source.value = null; sourceResource.clear(); await store.activate(null);
    spaceId.value = spaces[0]?.id || ''; documentScope.value = '';
    if (route.params.conversationId) await router.replace('/chat');
    await store.list(spaceId.value);
  }
}, { immediate: true });
watch(() => route.query.space, async id => { if (id && workspace.spaces.some(s => s.id === id)) { spaceId.value = id; await store.activate(null); await store.list(id); } });
async function switchSpace() { workspace.select(spaceId.value); source.value = null; await router.push('/chat'); await store.activate(null); store.list(spaceId.value); }
async function newConversation() { historyOpen.value = false; if (route.params.conversationId) await router.push('/chat'); else store.activate(null); question.value = ''; }
async function submit(text = question.value) {
  if (!text.trim() || !spaceId.value || store.busy || creating.value) return;
  actionError.value = null;
  const reading = readingOptions();
  try {
    if (!store.current) { creating.value = true; const conversation = await store.create(spaceId.value); await router.push(`/chat/${conversation.id}`); if (store.current?.id !== conversation.id) await store.activate(conversation.id); }
    question.value = ''; const sending = store.send(text, reading); await nextTick(); await scrollBottom(); await sending; await scrollBottom();
    if (store.error && !store.turns.some(turn => turn.original_question === text.trim())) question.value = text;
  } catch (cause) { actionError.value = cause; question.value = text; } finally { creating.value = false; }
}
function keydown(event) { if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) { event.preventDefault(); submit(); } }
async function scrollBottom() { await nextTick(); if (bodyElement.value) bodyElement.value.scrollTop = bodyElement.value.scrollHeight; }
watch(() => store.turns.map(turn => `${turn.id}:${turn.state}`).join(','), scrollBottom);
const timer = setInterval(store.poll, 3000);
onUnmounted(() => { clearInterval(timer); store.activate(null); });
function citedText(turn) {
  let answer = turn.result?.verified ? turn.result.answer || '' : '';
  for (const [index, citation] of (turn.result?.citations || []).entries()) answer = answer.replaceAll(`[${citation.evidence_id}]`, `[${index + 1}](#citation-${citation.evidence_id})`);
  return answer;
}
function inlineCitation(event, turn) { const link = event.target.closest('a[href^="#citation-"]'); if (link) { event.preventDefault(); const evidenceId = link.getAttribute('href').slice('#citation-'.length); const citation = turn.result?.citations.find(item => item.evidence_id === evidenceId); if (citation) openSource(citation); } }
function openSource(citation) { source.value = citation; sourceResource.clear(); sourceResource.load(); }
async function copy(turn) { try { await navigator.clipboard.writeText(turn.result.answer); copied.value = turn.id; } catch (cause) { actionError.value = cause; } }
async function rate(turn, rating) { try { await request(`/rag-runs/${turn.rag_run_id}/feedback`, { method: 'POST', body: JSON.stringify({ rating, reason_code: rating === 5 ? 'HELPFUL' : 'NOT_HELPFUL', comment: '' }) }); feedback.value[turn.id] = rating; } catch (cause) { actionError.value = cause; } }
async function rename() { renameBusy.value = true; try { await store.update(store.current.id, { title: title.value.trim() }); renameOpen.value = false; } catch (cause) { actionError.value = cause; } finally { renameBusy.value = false; } }
async function archive() { try { await store.update(store.current.id, { archived: true }); await router.push('/chat'); } catch (cause) { actionError.value = cause; } }
const stageLabels = { queued: '等待开始', resolving: '理解问题与对话上下文', retrieving: '检索当前资料并验证答案' };
function verificationProgress(turn) {
  const execution = turn.result?.coverage_report?.execution_failure;
  if (execution) return `失败步骤：${execution.stage === 'conversation_context' ? '解析本轮问题与会话上下文' : '知识问答执行'}。请求编号：${execution.request_id || turn.id}`;
  const detail = turn.result?.coverage_report?.verification_failure;
  if (!detail) return '';
  if (detail.stage === 'citation_repair') return '失败步骤：补齐回答引用；原回答尚未通过完整核验。';
  if (detail.stage === 'answer_surface_repair') return '失败步骤：按已核对事实重组回答；尚未通过完整核验。';
  if (detail.stage === 'condition_repair') return '失败步骤：补全回答遗漏的条件。';
  if (detail.stage === 'claims_and_conflicts') return '失败步骤：回答、引用与完整证据冲突核验。';
  if (detail.stage === 'conditions' && detail.batch_number) return `失败步骤：条件核验第 ${detail.batch_number} / ${detail.batch_count} 批；已完成 ${detail.completed_batches} 批，共 ${detail.condition_count} 条条件。`;
  if (detail.condition_count) return `待核验条件：${detail.condition_count} 条，共需 ${detail.batch_count} 批。`;
  return '';
}
function resultMessage(turn) {
  const result = turn.result || {};
  const status = result.status?.toUpperCase();
  if (result.sources_stale) return '这条回答的来源已撤回、更新或暂时无法校验。请重新生成，以当前有效资料为准。';
  if (turn.state === 'cancelled') return '已停止本轮回答。';
  const execution = result.coverage_report?.execution_failure;
  const code = execution?.code || turn.error_code;
  if (execution?.stage === 'conversation_context') {
    if (code === 'MODEL_PROVIDER_RATE_LIMITED') return '解析本轮问题时，上游模型返回 HTTP 429，本轮尚未开始知识检索。请稍后重试；若持续出现，请管理员检查模型网关或账户配额。';
    if (code === 'MODEL_ACCOUNT_REQUEST_EXCEEDS_TOKEN_BUDGET') return '会话解析请求超过账户单次可用预算，本轮尚未开始知识检索。请管理员检查请求预算配置。';
    if (code?.includes('TIMEOUT') || code?.includes('DEADLINE')) return '解析本轮问题时超时，本轮尚未开始知识检索。请查看技术详情中的请求编号。';
    if (execution.http_status) return `会话解析接口拒绝请求（HTTP ${execution.http_status}），本轮尚未开始知识检索。请查看技术详情。`;
    return '本轮问题的上下文解析未完成，尚未开始知识检索。具体原因和请求编号见技术详情。';
  }
  if (code === 'ProviderRateLimited') return '模型调用受到限流或额度限制，本轮未完成。请稍后重试；若持续出现，请管理员检查模型调用记录。';
  if (code === 'ProviderTimeout' || code === 'REQUEST_DEADLINE_EXCEEDED') return '本轮处理超时，请稍后重试；具体请求信息见技术详情。';
  const lastModelCall = result.coverage_report?.performance?.events?.filter(e => e.kind === 'model_http').at(-1);
  if (result.warnings?.includes('MODEL_ACCOUNT_REQUEST_EXCEEDS_TOKEN_BUDGET')) return '模型请求超过账户单次可用预算，本轮未完成。请管理员检查调用预算配置。';
  if (!result.verified && (result.warnings?.includes('MODEL_PROVIDER_RATE_LIMITED') || lastModelCall?.outcome === '429')) return '上游模型服务触发限流（HTTP 429），本轮未完成回答。请稍后重试；若持续出现，请管理员检查模型账户配额与网关限制。';
  if (result.warnings?.includes('VERIFIER_CONDITION_BUDGET_EXCEEDED')) return '已找到相关资料，但待核验条件超过本轮处理容量，本轮未展示回答。请缩小资料范围，或将技术详情中的运行编号提供给维护人员调整核验容量。';
  if (result.warnings?.includes('VERIFIER_CONDITION_REPAIR_BUDGET_EXCEEDED')) return '核验发现回答遗漏了条件，但补全所需资料超过本轮预算，本轮未展示回答。请缩小资料范围，或提供技术详情中的运行编号以便调整补全预算。';
  if (result.warnings?.includes('MODEL_PROVIDER_MODEL_UNSUPPORTED')) return '当前核验接口不支持配置的模型，回答未能完成验证。请管理员调整核验模型配置。';
  if (result.warnings?.includes('MODEL_PROVIDER_HTTP_ERROR')) return `核验接口拒绝了请求（HTTP ${result.coverage_report?.verification_failure?.http_status || '错误'}），本轮未展示回答。请查看技术详情中的失败步骤与运行编号。`;
  if (result.coverage_report?.verification_failure?.provider_code === 'MODEL_ACCOUNT_QUOTA_WAIT_TIMEOUT') return '答案核验等待账户额度超时，本轮未展示回答。可稍后重试；管理员可根据耗时详情调整调用预算。';
  if (result.warnings?.includes('CLAIM_VERIFIER_TIMEOUT')) return '已找到相关资料，但答案核验超时，本轮未展示回答。可以重新提问；若持续超时，请提供技术详情中的运行编号。';
  if (result.warnings?.includes('CLAIM_VERIFIER_UNAVAILABLE')) return '已找到相关资料，但答案核验服务暂时不可用，本轮未展示回答。可以稍后重新提问；若持续失败，请提供技术详情中的运行编号。';
  if (result.warnings?.some(code => code.startsWith('CITATION_REPAIR_'))) return '已找到相关资料，但引用补全未完成，本轮未展示回答。请查看技术详情中的失败步骤与运行编号。';
  if (result.warnings?.some(code => ['VERIFIER_VERDICT_COUNT_INVALID', 'VERIFIER_CLAIM_ID_INVALID'].includes(code))) return '已找到相关资料，但核验返回的结论数量或编号与待核验内容不一致，本轮未展示回答。请提供技术详情中的运行编号以便排查。';
  if (result.warnings?.includes('CLAIM_VERIFIER_PROTOCOL_INVALID')) return '已找到相关资料，但答案核验未完成：核验结果格式或引用校验不符合要求，本轮未展示回答。请将技术详情中的运行编号提供给维护人员排查。';
  if (result.warnings?.includes('GENERATION_PROTOCOL_INVALID')) return '已找到相关资料，但答案生成未完成。可重新提问，或提供技术详情中的运行编号以便排查。';
  if (result.warnings?.includes('SOURCE_LIST_COVERAGE_INCOMPLETE')) return '已找到原文编号清单，但整理后的条目有缺失、改写或顺序变化，本轮未展示回答。请查看技术详情中的运行编号。';
  if (result.warnings?.includes('ANSWER_CITATION_COVERAGE_INVALID')) return '已找到资料，但生成回答的事实与引用未能完整对应，本轮未展示回答。请查看技术详情中的运行编号以便排查。';
  if (result.warnings?.includes('ANSWER_NOT_SUPPORTED') && !result.warnings?.includes('ANSWER_KEY_CONDITION_MISSING')) return '已找到资料，但生成回答中有内容未通过原文核验，本轮未展示回答。请查看技术详情中的运行编号；这不代表知识库中没有相关资料。';
  if (turn.state === 'failed') return '本轮处理未完成，可以重新提问。';
  if (status === 'NEEDS_CLARIFICATION') return result.clarification_question || '请补充问题中的具体产品、对象或必要条件。';
  if (status === 'OUT_OF_SCOPE') return '当前知识问答支持基于资料回答问题，请描述你希望了解的具体信息。';
  if (result.warnings?.includes('ANSWER_KEY_CONDITION_MISSING')) return '答案未完整保留资料中的适用条件或例外，补全后仍未通过核对，因此没有展示。';
  if (visualEvidenceNotice(result)) return visualEvidenceNotice(result);
  if (status === 'INSUFFICIENT_EVIDENCE') return '本轮检索到的资料不足以支持回答。可以补充对象、版本或条件后再试。';
  return '本轮未得到通过验证的答案。可以补充问题、检查资料或重新提问。';
}
</script>
<template><div class="chat-layout" :class="{ 'source-open': source }">
  <button v-if="historyOpen" class="history-backdrop" aria-label="关闭会话列表" @click="historyOpen = false"/>
  <aside :class="['chat-history', { open: historyOpen }]"><div class="chat-history-heading"><h2>知识问答</h2><span class="mini-label">CHAT</span></div><button class="btn new-chat-button" @click="newConversation"><Plus :size="18"/>新建对话</button><label class="field-label" for="chat-space">当前知识库</label><select id="chat-space" v-model="spaceId" :disabled="creating" @change="switchSpace"><option value="" disabled>选择知识库</option><option v-for="space in workspace.spaces" :key="space.id" :value="space.id">{{ space.name }}</option></select><p class="nav-caption">会话记录</p><div class="conversation-list"><RouterLink v-for="conversation in store.conversations" :key="conversation.id" :to="`/chat/${conversation.id}`" :class="['conversation-link', { active: route.params.conversationId === conversation.id }]"><MessagesSquare :size="16"/><div><strong>{{ conversation.title }}</strong><small>{{ dateTime(conversation.updated_at) }}</small></div><span v-if="conversation.active_turn_id" class="stage-dot running"/></RouterLink><p v-if="!store.conversations.length" class="small muted history-empty">你的对话会自动保存在这里。</p><button v-if="store.listCursor" class="btn ghost" @click="store.list(spaceId, true)">更多会话</button></div><div class="history-note"><BookOpen :size="17"/><p>每个会话固定一个知识库<br/>答案仅来自当前有效资料</p></div></aside>
  <section class="chat-main"><header class="chat-topbar"><button class="icon-button chat-menu" aria-label="展开会话历史" @click="historyOpen = !historyOpen"><PanelLeft :size="19"/></button><div class="grow truncate"><strong>{{ store.current?.title || '新的知识探索' }}</strong><span class="small muted">{{ currentSpace?.name || '请先选择知识库' }}</span></div><template v-if="store.current"><button class="icon-button" aria-label="重命名对话" @click="title = store.current.title; renameOpen = true"><Pencil :size="16"/></button><button class="icon-button" aria-label="归档对话" :disabled="store.busy" @click="archive"><Archive :size="17"/></button></template></header>
    <div ref="bodyElement" class="chat-scroll"><div v-if="!store.turns.length && !store.loading" class="chat-welcome"><div class="ask-symbol"><Sparkles :size="33"/></div><p class="eyebrow">ANSWERS, GROUNDED IN KNOWLEDGE</p><h1>让知识，回答你的问题。</h1><p>从「{{ currentSpace?.name || '你的知识库' }}」中寻找答案，<br/>每个结论都可以回到原文。</p><div class="chat-principles"><span><BookOpen :size="16"/>基于资料</span><span><Check :size="16"/>验证后呈现</span><span><MessagesSquare :size="16"/>支持上下文追问</span></div><RouterLink v-if="currentSpace && !currentSpace.answerable_count && auth.canManage(spaceId)" :to="`/knowledge-bases/${spaceId}/documents`" class="notice warning"><FileText :size="18"/><span>这个知识库还没有可问答文档，先检查并发布资料。</span><ArrowUpRight :size="17"/></RouterLink><div v-else-if="currentSpace && !currentSpace.answerable_count" class="notice warning"><FileText :size="18"/><span>这个知识库还没有可问答资料，请联系知识库管理员。</span></div></div>
    <div v-if="store.loading && !store.turns.length" class="chat-loading"><LoaderCircle class="spin" :size="22"/>正在恢复对话…</div><button v-if="store.nextBefore" class="btn ghost load-more" :disabled="store.loading" @click="store.older">查看更早的对话</button>
    <div class="message-list"><article v-for="turn in store.turns" :key="turn.id" class="conversation-turn"><div class="user-message"><span>你</span><p>{{ turn.original_question }}</p></div><div class="assistant-message"><span class="assistant-avatar"><Sparkles :size="18"/></span><div class="assistant-body"><div class="assistant-label">知识助手<span v-if="turn.result?.verified && turn.result.answer" class="verified-label"><Check :size="12"/>已验证</span></div><div v-if="['queued','resolving','retrieving'].includes(turn.state)" class="answer-processing"><LoaderCircle :size="16" class="spin"/>{{ stageLabels[turn.state] }}<span class="typing-dots">…</span></div><template v-else-if="turn.result?.verified && turn.result.answer"><div @click="inlineCitation($event, turn)"><MarkdownContent :text="citedText(turn)"/></div><p v-if="turn.result.coverage === 'partial'" class="answer-notice">以上仅回答资料能够支持的部分，其余问题在本轮检索中未找到充分依据。</p><p v-if="visualEvidenceNotice(turn.result)" class="answer-notice">{{ visualEvidenceNotice(turn.result) }}</p><div class="citation-list"><button v-for="(citation, i) in turn.result.citations" :key="citation.evidence_id" class="citation-button" @click="openSource(citation)"><span>{{ i + 1 }}</span><FileText :size="13"/><span class="truncate">{{ citation.filename }}</span><ArrowUpRight :size="13"/></button></div><div class="answer-actions"><button class="icon-button" :aria-label="copied === turn.id ? '已复制' : '复制答案'" @click="copy(turn)"><Check v-if="copied === turn.id" :size="15"/><Copy v-else :size="15"/></button><button class="icon-button" aria-label="答案有帮助" :class="{ accent: feedback[turn.id] === 5 }" @click="rate(turn, 5)"><ThumbsUp :size="15"/></button><button class="icon-button" aria-label="答案无帮助" :class="{ accent: feedback[turn.id] === 1 }" @click="rate(turn, 1)"><ThumbsDown :size="15"/></button><span v-if="feedback[turn.id]" class="small muted">反馈已记录</span></div></template><template v-else><p class="answer-notice">{{ resultMessage(turn) }}</p><button v-if="turn.state === 'failed' || turn.result?.retryable" class="btn small-button" :disabled="store.busy" @click="submit(turn.original_question)">重新提问</button></template><RouterLink v-if="auth.canManage(spaceId) && !['queued','resolving','retrieving'].includes(turn.state)" class="text-button" :to="`/acceptance/${spaceId}?conversation=${route.params.conversationId}&turn=${turn.id}`">加入验收案例</RouterLink><ConditionCoverage :report="turn.result?.coverage_report?.conditions"/><AnswerPerformance :report="turn.result?.coverage_report?.performance"/><div v-if="turn.result?.coverage_report?.mode || turn.reading_progress?.mode" class="reading-coverage"><template v-for="report in [turn.result?.coverage_report?.mode ? turn.result.coverage_report : turn.reading_progress]" :key="turn.id"><p>逐章阅读：{{ report.read_sections || 0 }} / {{ report.total_sections || 0 }} 章 · {{ report.read_chunks || 0 }} 个片段</p><ReadingImageCoverage :report="report"/><p v-if="report.complete === false" class="accent-warning">本轮覆盖不完整，请结合缺口阅读回答。</p><details v-if="report.gaps?.length"><summary>查看未覆盖内容（{{ report.gaps.length }}）</summary><ul><li v-for="gap in report.gaps" :key="gap">{{ gap }}</li></ul></details><details v-if="report.sections?.length"><summary>查看章节与资料范围</summary><p v-for="doc in report.source_documents" :key="doc.document_id">{{ doc.filename }}</p><p v-for="section in report.sections" :key="section.version_id+section.section">{{ section.section }} · {{ section.usable_chunks }} / {{ section.chunks }} 个可用片段</p><p v-if="report.cross_image_links?.length">已核对 {{ report.cross_image_links.length }} 处明确图号引用，未按同名节点推断关系。</p></details></template></div><details v-if="turn.resolved_question && turn.resolved_question !== turn.original_question" class="resolved-question"><summary>本轮检索问题</summary><p>{{ turn.resolved_question }}</p></details><details v-if="turn.error_code || (!turn.result?.verified && (turn.result?.warnings?.length || turn.rag_run_id || turn.result?.rag_run_id))" class="technical"><summary>技术详情</summary><p v-if="verificationProgress(turn)">{{ verificationProgress(turn) }}</p><p v-if="turn.rag_run_id || turn.result?.rag_run_id">运行编号：<code>{{ turn.rag_run_id || turn.result.rag_run_id }}</code></p><p v-if="turn.error_code"><code>{{ turn.error_code }}</code></p><p v-for="warning in turn.result?.warnings || []" :key="warning"><code>{{ warning }}</code></p></details></div></div></article></div>
    </div><div class="composer-area"><p v-if="!workspace.spaces.length" class="auth-message">尚未分配知识库，请联系管理员。</p><ErrorNotice :error="store.error || actionError"/><div class="reading-controls"><label>阅读方式<select v-model="readingMode" :disabled="store.busy"><option value="auto">自动判断</option><option value="fact">查找具体问题</option><option value="overview">逐章总结</option><option value="compare">跨图 / 多文档综合</option></select></label><label v-if="auth.canManage(spaceId)">资料范围<select v-model="documentScope" :disabled="store.busy"><option value="">当前知识库</option><option v-for="document in readableDocuments" :key="document.document_id" :value="document.document_id">{{ document.filename }}</option></select></label><button v-if="documentCursor" class="btn small-button" :disabled="documentsBusy" @click="loadReadable(true)">更多文档</button></div><div class="composer"><textarea v-model="question" aria-label="向知识库提问" rows="2" maxlength="4000" :placeholder="store.turns.length ? '继续追问，或提出新的问题…' : '向知识库提问…'" :disabled="creating || !workspace.spaces.length" @keydown="keydown"/><div class="composer-bottom"><span><BookOpen :size="14"/>{{ currentSpace?.name || '未选择知识库' }}</span><button v-if="store.busy" class="stop-button" :disabled="!store.activeTurn" @click="store.cancel"><Square :size="13"/>停止回答</button><button v-else class="send-button" :disabled="!question.trim() || !spaceId || creating" aria-label="发送问题" @click="submit()"><Send :size="17"/></button></div></div><p class="composer-hint">Enter 发送 · Shift + Enter 换行<span>答案通过验证后显示，请结合原文判断。</span></p></div>
  </section>
  <aside v-if="source" class="source-panel"><header><div><p class="eyebrow">SOURCE REFERENCE</p><h2>原文引用</h2></div><button class="icon-button" aria-label="关闭来源" @click="source = null; sourceResource.clear()"><X :size="20"/></button></header><div class="source-file"><FileText :size="24"/><div><strong>{{ source.filename }}</strong><p class="small muted">版本 {{ source.version_no }} · {{ locationLabel(source.locator) }}</p></div></div><ErrorNotice :error="sourceResource.error.value"/><div v-if="sourceResource.loading.value" class="chat-loading"><LoaderCircle class="spin" :size="20"/>正在校验来源…</div><div v-if="sourceResource.data.value" class="source-text"><VisualAsset v-for="asset in sourceResource.data.value.visuals || []" :key="asset.id" :asset="asset" compact/><details v-if="sourceResource.data.value.visuals?.length" class="visual-source-text"><summary>查看引用文字</summary><MarkdownContent :text="sourceResource.data.value.text" source-tables/></details><MarkdownContent v-else :text="sourceResource.data.value.text" source-tables/></div><RouterLink v-if="sourceResource.data.value && auth.canManage(spaceId)" :to="`/knowledge-bases/${spaceId}/documents/${source.document_id}?version=${source.version_id}&chunk=${source.chunk_id}`" class="btn source-document-link">打开文档<ArrowUpRight :size="16"/></RouterLink></aside>
  <AppDialog :open="renameOpen" title="重命名对话" @close="renameOpen = false"><form @submit.prevent="rename"><label class="field-label" for="conversation-title">对话名称</label><input id="conversation-title" v-model="title" required maxlength="100"/><ErrorNotice :error="actionError"/><div class="dialog-actions"><button type="button" class="btn" @click="renameOpen = false">取消</button><button class="btn primary" :disabled="renameBusy || !title.trim()">保存名称</button></div></form></AppDialog>
</div></template>
