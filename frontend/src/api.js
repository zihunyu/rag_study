import { accessToken } from "./auth.js";

const runtimeBase = globalThis.__RAGKB_CONFIG__?.apiBaseUrl?.trim();
const configuredBase = runtimeBase || import.meta.env?.VITE_API_BASE_URL?.trim();

export const API_BASE = (configuredBase || "/api/v1").replace(/\/$/, "");

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
  tokenProvider = accessToken,
) {
  const token = await tokenProvider();
  const headers = new Headers(options.headers ?? {});
  if (token) headers.set("Authorization", `Bearer ${token}`);
  return fetchImpl(url, { ...options, headers });
}

export async function request(path, options = {}, fetchImpl = fetch) {
  const response = await authorizedFetch(apiUrl(path), {
    headers: { "Content-Type": "application/json", ...(options.headers ?? {}) },
    ...options,
  }, fetchImpl);
  const body = await response.json();
  if (!response.ok) throw new Error(body.code ?? "REQUEST_FAILED");
  return body;
}

export async function consumeSSE(response, onEvent) {
  if (!response.ok || !response.body) throw new Error("SSE_REQUEST_FAILED");
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { done, value } = await reader.read();
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

export async function askStream(question, onProgress, fetchImpl = fetch) {
  const response = await authorizedFetch(apiUrl("/ask:stream"), {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
    body: JSON.stringify({ question }),
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
