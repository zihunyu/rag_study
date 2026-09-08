// Shared cookie/CSRF transport. No credentials or session tokens enter browser storage.
let csrf = '', mode = '', epoch = 0;
const listeners = new Set(), requests = new Set();
export const authMode = () => mode;
export const authEpoch = () => epoch;
export const csrfToken = () => csrf;
export function configureSession(value, token = '') { mode = value; csrf = token; }
export function onSessionReset(callback) { listeners.add(callback); return () => listeners.delete(callback); }
export function resetSession(reason = 'logout') {
  epoch++; csrf = '';
  for (const controller of requests) controller.abort();
  requests.clear();
  for (const callback of listeners) callback(reason);
  for (const storage of [globalThis.localStorage, globalThis.sessionStorage]) {
    if (!storage) continue;
    for (let i = storage.length - 1; i >= 0; i--) {
      const key = storage.key(i);
      if (key?.startsWith('ragkb.') || key?.startsWith('ragspace-publication:')) storage.removeItem(key);
    }
  }
}
export async function sessionFetch(url, options = {}, fetchImpl = fetch) {
  const own = epoch, controller = new AbortController(); requests.add(controller);
  const abort = () => controller.abort();
  if (options.signal?.aborted) abort();
  options.signal?.addEventListener('abort', abort, { once: true });
  const headers = new Headers(options.headers || {});
  try {
    if (mode === 'password' && !['GET', 'HEAD', 'OPTIONS'].includes((options.method || 'GET').toUpperCase())) {
      if (!csrf) throw new Error('SESSION_CSRF_UNAVAILABLE');
      headers.set('X-CSRF-Token', csrf);
    }
    const { silentUnauthorized, ...fetchOptions } = options;
    const response = await fetchImpl(url, { ...fetchOptions, headers, credentials: 'include', signal: controller.signal });
    if (own !== epoch) throw new DOMException('Account changed', 'AbortError');
    if (response.status === 401 && !silentUnauthorized && !String(url).includes('/auth/login') && mode === 'password') resetSession('expired');
    return response;
  } finally {
    requests.delete(controller);
    options.signal?.removeEventListener('abort', abort);
  }
}
