import { defineStore } from 'pinia';
import { ref, computed } from 'vue';
import { apiUrl, command, request, requestPage, queryString, consumeSSE } from '../api.js';
const activeStates = new Set(['queued', 'resolving', 'retrieving']);
export const useConversations = defineStore('conversations', () => {
  const conversations = ref([]), listCursor = ref(null), current = ref(null), turns = ref([]), nextBefore = ref(null), loading = ref(false), sending = ref(false), error = ref(null);
  let revision = 0, listRevision = 0, controller = null, loadingController = null;
  const activeTurn = computed(() => turns.value.find(turn => activeStates.has(turn.state)));
  const busy = computed(() => sending.value || !!activeTurn.value);
  function merge(turn) {
    if (turn.result && !turn.result.verified) turn.result = { ...turn.result, answer: null, citations: [] };
    const index = turns.value.findIndex(row => row.id === turn.id);
    if (index === -1) turns.value.push(turn); else turns.value[index] = { ...turns.value[index], ...turn };
    turns.value.sort((a, b) => a.sequence_number - b.sequence_number);
  }
  async function list(spaceId = '', more = false) {
    const own = ++listRevision;
    try { const page = await requestPage(`/conversations?${queryString({ space_id: spaceId, limit: 30, cursor: more ? listCursor.value : null })}`); if (own === listRevision) { conversations.value = more ? [...conversations.value, ...page.items.filter(row => !conversations.value.some(old => old.id === row.id))] : page.items; listCursor.value = page.nextCursor; } }
    catch (cause) { if (own === listRevision) error.value = cause; }
  }
  async function activate(id) {
    const own = ++revision; controller?.abort(); loadingController?.abort();
    current.value = null; turns.value = []; nextBefore.value = null; error.value = null; sending.value = false; loading.value = !!id;
    if (!id) return;
    loadingController = new AbortController();
    try { const data = await request(`/conversations/${id}`, { signal: loadingController.signal }); if (own === revision) { current.value = data.conversation; data.turns.forEach(merge); nextBefore.value = data.next_before; } }
    catch (cause) { if (own === revision && cause.name !== 'AbortError') error.value = cause; }
    finally { if (own === revision) loading.value = false; }
  }
  async function older() {
    if (!current.value || !nextBefore.value || loading.value) return;
    const own = revision; loading.value = true;
    try { const data = await request(`/conversations/${current.value.id}?before=${nextBefore.value}`); if (own === revision) { data.turns.forEach(merge); nextBefore.value = data.next_before; } }
    catch (cause) { if (own === revision) error.value = cause; } finally { if (own === revision) loading.value = false; }
  }
  async function poll() {
    if (!current.value || !activeTurn.value || sending.value) return;
    const own = revision, id = current.value.id, turnId = activeTurn.value.id;
    try { const turn = await request(`/conversations/${id}/turns/${turnId}`); if (own === revision) merge(turn); }
    catch (cause) { if (own === revision) error.value = cause; }
  }
  async function create(spaceId) { const conversation = await command('/conversations', { space_id: spaceId }); await list(spaceId); return conversation; }
  async function send(question, reading = {}) {
    if (!current.value || busy.value || !question.trim()) return;
    const own = revision, id = current.value.id;
    sending.value = true; error.value = null; controller = new AbortController();
    const key = `ragkb.pending-turn.${id}`;
    let pending;
    try { pending = JSON.parse(sessionStorage.getItem(key)); } catch { /* malformed local state */ }
    if (pending?.question !== question.trim() || JSON.stringify(pending?.reading || {}) !== JSON.stringify(reading)) pending = { question: question.trim(), reading, client_request_id: crypto.randomUUID() };
    sessionStorage.setItem(key, JSON.stringify(pending));
    try {
      const response = await fetch(apiUrl(`/conversations/${id}/turns:stream`), { method: 'POST', signal: controller.signal, headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream' }, body: JSON.stringify(pending) });
      if (!response.ok) { const body = await response.json(); const cause = new Error(body.detail || body.code || 'CONVERSATION_SEND_FAILED'); cause.status = response.status; throw cause; }
      let received = false;
      await consumeSSE(response, (event, payload) => {
        if (own !== revision) return;
        if (event === 'turn' || event === 'result') { merge(payload); sessionStorage.removeItem(key); }
        if (event === 'result') received = true;
      });
      if (!received && own === revision) await pollAfterStream(id, own);
    } catch (cause) {
      if (own === revision && cause.name !== 'AbortError') { error.value = cause; await pollAfterStream(id, own); }
    } finally { if (own === revision) { sending.value = false; await list(current.value?.space_id || ''); } }
  }
  async function pollAfterStream(id, own) { try { const data = await request(`/conversations/${id}`); if (own === revision) { current.value = data.conversation; data.turns.forEach(merge); } } catch { /* original network error stays visible */ } }
  async function cancel() {
    if (!current.value || !activeTurn.value) return;
    const own = revision;
    try { const turn = await command(`/conversations/${current.value.id}/turns/${activeTurn.value.id}:cancel`); if (own === revision) { controller?.abort(); merge(turn); sending.value = false; } }
    catch (cause) { if (own === revision) error.value = cause; }
  }
  async function update(id, patch) {
    const result = await request(`/conversations/${id}`, { method: 'PATCH', body: JSON.stringify(patch) });
    if (current.value?.id === id) current.value = result;
    await list(result.space_id); return result;
  }
  return { conversations, listCursor, current, turns, nextBefore, loading, sending, error, activeTurn, busy, list, activate, older, poll, create, send, cancel, update };
});
