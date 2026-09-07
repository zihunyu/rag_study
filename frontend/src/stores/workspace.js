import { defineStore } from 'pinia';
import { computed, ref } from 'vue';
import { request } from '../api.js';
export const useWorkspace = defineStore('workspace', () => {
  const spaces = ref([]), totals = ref({}), loading = ref(false), error = ref(null), capabilities = ref(null);
  const selectedId = ref(localStorage.getItem('ragkb.selected-space') || '');
  const selected = computed(() => spaces.value.find(s => s.id === selectedId.value));
  let revision = 0;
  function select(id) { selectedId.value = id; localStorage.setItem('ragkb.selected-space', id); }
  async function refresh() {
    const own = ++revision; loading.value = true; error.value = null;
    try { const value = await request('/spaces/overview'); if (own !== revision) return;
      spaces.value = value.items; totals.value = value.totals;
      if (!spaces.value.some(s => s.id === selectedId.value)) select(spaces.value[0]?.id ?? '');
    } catch (cause) { if (own === revision) error.value = cause; }
    finally { if (own === revision) loading.value = false; }
  }
  async function init() { await Promise.all([refresh(), request('/capabilities').then(v => { capabilities.value = v; }).catch(() => {})]); }
  return { spaces, totals, loading, error, selectedId, selected, capabilities, select, refresh, init };
});
