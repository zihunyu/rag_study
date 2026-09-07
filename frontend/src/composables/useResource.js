import { onUnmounted, ref, shallowRef } from 'vue';
export function useResource(loader) {
  const data = shallowRef(null), loading = ref(false), error = shallowRef(null);
  let revision = 0, controller;
  async function load() {
    const own = ++revision; controller?.abort(); controller = new AbortController();
    loading.value = true; error.value = null;
    try { const result = await loader(controller.signal); if (own === revision) data.value = result; return own === revision ? result : null; }
    catch (cause) { if (own === revision && cause.name !== 'AbortError') error.value = cause; return null; }
    finally { if (own === revision) loading.value = false; }
  }
  function clear() { ++revision; controller?.abort(); data.value = null; error.value = null; loading.value = false; }
  onUnmounted(() => { ++revision; controller?.abort(); });
  return { data, loading, error, load, clear };
}
