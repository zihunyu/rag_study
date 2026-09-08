import { ref, watch, onUnmounted } from 'vue';
import { authEpoch, onSessionReset, sessionFetch } from '../authTransport.js';

export function usePrivateImage(url) {
  const image = ref(''), failed = ref(false);
  let controller, revision = 0;
  function clear() { ++revision; controller?.abort(); if (image.value) URL.revokeObjectURL(image.value); image.value = ''; }
  const unsubscribe = onSessionReset(clear);
  watch(url, async value => {
    clear(); failed.value = false; if (!value) return;
    const own = revision, account = authEpoch(); controller = new AbortController();
    try {
      const response = await sessionFetch(value, { signal: controller.signal });
      if (!response.ok) throw new Error('SOURCE_REFERENCE_NOT_FOUND');
      const blob = await response.blob();
      if (own === revision && account === authEpoch()) image.value = URL.createObjectURL(blob);
    } catch (error) { if (own === revision && error.name !== 'AbortError') failed.value = true; }
  }, { immediate: true });
  onUnmounted(() => { unsubscribe(); clear(); });
  return { image, failed };
}
