import { authEpoch, sessionFetch } from './authTransport.js';
const runtimeBase = globalThis.__RAGKB_CONFIG__?.apiBaseUrl?.trim();
const configuredBase = runtimeBase || import.meta.env?.VITE_API_BASE_URL?.trim();

export const API_BASE = (configuredBase || "/api").replace(/\/$/, "");

export function apiUrl(path) {
  return `${API_BASE}${path.startsWith("/") ? path : `/${path}`}`;
}

export function sourceUrl(path, apiBase = API_BASE) {
  if (/^https?:\/\//i.test(path)) return path;
  if (/^https?:\/\//i.test(apiBase)) return new URL(path, apiBase).toString();
  return path;
}

export async function authorizedFetch(
  url,
  options = {},
  fetchImpl = fetch,
  tokenProvider = async () => null,
) {
  const token = await tokenProvider();
  const headers = new Headers(options.headers ?? {});
  if (token) headers.set("Authorization", `Bearer ${token}`);
  return sessionFetch(url, { ...options, headers }, fetchImpl);
}

export async function request(path, options = {}, fetchImpl = fetch) {
  return (await requestResponse(path, options, fetchImpl)).body;
}

export async function requestPage(path, options = {}, fetchImpl = fetch) {
  const { body, response } = await requestResponse(path, options, fetchImpl);
  return { items: body, nextCursor: response.headers.get("X-Next-Cursor") || null };
}

export async function requestResponse(path, options = {}, fetchImpl = fetch) {
  const account = authEpoch();
  const { headers: optionHeaders, ...requestOptions } = options;
  const response = await authorizedFetch(apiUrl(path), {
    ...requestOptions,
    headers: { "Content-Type": "application/json", ...(optionHeaders ?? {}) },
  }, fetchImpl);
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    const validationCode = Array.isArray(body.detail)
      ? body.detail
          .map((item) => `${item.loc?.slice(1).join(".") || "request"}:${item.msg || item.type}`)
          .join(";")
      : null;
    const detailCode = typeof body.detail === "string"
      ? body.detail
      : body.detail?.code;
    const error = new Error(
      body.code ?? validationCode ?? detailCode ?? `REQUEST_FAILED_HTTP_${response.status}`,
    );
    error.status = response.status;
    error.retryAfter = Number(response.headers.get('Retry-After') || 0);
    error.requestId = response.headers.get('X-Request-ID');
    throw error;
  }
  if (account !== authEpoch()) throw new DOMException('Account changed', 'AbortError');
  return { body, response };
}

export const command = (path, body = {}, key = crypto.randomUUID()) => request(path, {
  method: 'POST', headers: { 'Idempotency-Key': key }, body: JSON.stringify(body),
});
export function queryString(values) {
  return new URLSearchParams(Object.entries(values).filter(([, value]) => value !== '' && value != null)).toString();
}
export async function jobCommand(jobId, action) {
  const { response } = await requestResponse(`/ingestion-jobs/${jobId}`);
  return request(`/ingestion-jobs/${jobId}:${action}`, { method: 'POST',
    headers: { 'If-Match': response.headers.get('ETag'), 'Idempotency-Key': crypto.randomUUID() },
  });
}

export async function consumeSSE(response, onEvent) {
  const account = authEpoch();
  if (!response.ok || !response.body) throw new Error("SSE_REQUEST_FAILED");
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { done, value } = await reader.read();
    if (account !== authEpoch()) { await reader.cancel(); throw new DOMException('Account changed', 'AbortError'); }
    buffer += decoder.decode(value ?? new Uint8Array(), { stream: !done });
    buffer = buffer.replace(/\r\n/g, "\n").replace(/\r/g, "\n");
    const frames = buffer.split("\n\n");
    buffer = frames.pop() ?? "";
    for (const frame of frames) {
      const lines = frame.split(/\r?\n/);
      const event = lines.find((line) => line.startsWith("event:"))?.slice(6).trim();
      const data = lines
        .filter((line) => line.startsWith("data:"))
        .map((line) => line.slice(5).trim())
        .join("\n");
      if (event && data) onEvent(event, JSON.parse(data));
    }
    if (done) break;
  }
}

export async function askStream(question, onProgress, fetchImpl = fetch, spaceId = null) {
  const response = await authorizedFetch(apiUrl("/ask:stream"), {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
    body: JSON.stringify({ question, ...(spaceId ? { space_id: spaceId } : {}) }),
  }, fetchImpl);
  let verified = false;
  let result = null;
  await consumeSSE(response, (event, payload) => {
    if (event === "progress") {
      verified ||= payload.stage === "verified";
      onProgress(payload.stage);
    }
    if (event === "result") {
      result = verified
        ? payload
        : { ...payload, answer: null, citations: [], verified: false };
    }
  });
  if (!result) throw new Error("SSE_RESULT_MISSING");
  return result;
}

// Keep a logical publication attempt across refreshes; a later withdrawal starts a new one.
export async function confirmPublication(versionId, comment) {
  const storageKey = `ragspace-publication:${versionId}`;
  let attempt;
  try { attempt = JSON.parse(sessionStorage.getItem(storageKey) || 'null'); } catch { /* damaged local metadata */ }
  if (!attempt?.key) { attempt = { key: crypto.randomUUID(), comment }; sessionStorage.setItem(storageKey, JSON.stringify(attempt)); }
  const result = await command(`/document-versions/${versionId}:review-and-publish`, { comment: attempt.comment }, attempt.key);
  if (result.phase === 'published') sessionStorage.removeItem(storageKey);
  return result;
}
