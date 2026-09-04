import assert from "node:assert/strict";
import test from "node:test";

import { askStream, authorizedFetch, request, sourceUrl } from "./api.js";

function sseResponse(frames) {
  const encoder = new TextEncoder();
  const body = new ReadableStream({
    start(controller) {
      for (const frame of frames) controller.enqueue(encoder.encode(frame));
      controller.close();
    },
  });
  return new Response(body, { status: 200, headers: { "Content-Type": "text/event-stream" } });
}

test("relative signed source uses configured backend origin", () => {
  assert.equal(
    sourceUrl("/api/v1/rag-runs/run/evidence/item/source", "http://127.0.0.1:8000/api/v1"),
    "http://127.0.0.1:8000/api/v1/rag-runs/run/evidence/item/source",
  );
});

test("SSE result is released only after verified progress", async () => {
  const progress = [];
  const fakeFetch = async () =>
    sseResponse([
      'event: progress\ndata: {"stage":"retrieval_started"}\n\n',
      'event: progress\ndata: {"stage":"verified"}\n\n',
      'event: result\ndata: {"answer":"ok","citations":[],"verified":true}\n\n',
    ]);
  const result = await askStream("question", (stage) => progress.push(stage), fakeFetch);
  assert.equal(result.answer, "ok");
  assert.deepEqual(progress, ["retrieval_started", "verified"]);
});

test("unverified SSE payload cannot expose buffered answer", async () => {
  const fakeFetch = async () =>
    sseResponse([
      'event: progress\ndata: {"stage":"verification_failed"}\n\n',
      'event: result\ndata: {"answer":"must-not-display","citations":[1],"verified":false}\n\n',
    ]);
  const result = await askStream("question", () => {}, fakeFetch);
  assert.equal(result.answer, null);
  assert.deepEqual(result.citations, []);
});

test("fragmented CRLF SSE frames are reassembled", async () => {
  const fakeFetch = async () =>
    sseResponse([
      'event: progress\r\ndata: {"stage":"ver',
      'ified"}\r\n\r\nevent: result\r\ndata: {"answer":"ok",',
      '"citations":[],"verified":true}\r\n\r\n',
    ]);

  const result = await askStream("question", () => {}, fakeFetch);

  assert.equal(result.answer, "ok");
});

test("closed SSE connection without result is explicit", async () => {
  const fakeFetch = async () =>
    sseResponse(['event: progress\ndata: {"stage":"verified"}\n\n']);

  await assert.rejects(() => askStream("question", () => {}, fakeFetch), {
    message: "SSE_RESULT_MISSING",
  });
});

test("non-success API response exposes only stable server code", async () => {
  const fakeFetch = async () =>
    new Response(JSON.stringify({ code: "PERMISSION_DENIED", detail: "secret" }), {
      status: 403,
      headers: { "Content-Type": "application/json" },
    });

  await assert.rejects(() => request("/search", {}, fakeFetch), {
    message: "PERMISSION_DENIED",
  });
});

test("production requests attach the OIDC bearer token", async () => {
  let observed;
  const fakeFetch = async (_url, options) => {
    observed = new Headers(options.headers);
    return new Response("{}", { status: 200 });
  };

  await authorizedFetch("https://api.example.test/api/v1/spaces", {}, fakeFetch, async () =>
    "signed-access-token",
  );

  assert.equal(observed.get("Authorization"), "Bearer signed-access-token");
});
