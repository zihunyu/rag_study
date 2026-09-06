import { flushPromises, mount } from "@vue/test-utils";
import { afterEach, describe, expect, it, vi } from "vitest";
import { webcrypto } from "node:crypto";

import App from "./App.vue";

vi.mock("./auth.js", () => ({
  initializeAuth: async () => ({ profile: { sub: "synthetic-reader" } }),
  accessToken: async () => "synthetic-browser-token",
  oidcEnabled: true, signIn: vi.fn(), signOut: vi.fn(),
}));

function sseResponse(payload) {
  const encoder = new TextEncoder();
  return new Response(
    new ReadableStream({
      start(controller) {
        controller.enqueue(encoder.encode(payload));
        controller.close();
      },
    }),
    { status: 200, headers: { "Content-Type": "text/event-stream" } },
  );
}

function button(wrapper, label) {
  return wrapper.findAll("button").find((item) => item.text().includes(label));
}

function jsonResponse(payload, status = 200, headers = {}) {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { "Content-Type": "application/json", ...headers },
  });
}

function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}

function uploadFile(name, content, read) {
  const bytes = new TextEncoder().encode(content);
  const file = new File([content], name, { type: "text/plain" });
  Object.defineProperty(file, "slice", { value: (start, end) => ({
    arrayBuffer: read ?? (async () => bytes.slice(start, end).buffer),
  }) });
  return file;
}

async function chooseFile(input, file) {
  Object.defineProperty(input.element, "files", { value: file ? [file] : [], configurable: true });
  await input.trigger("change");
  await flushPromises();
}

describe("question disposition results", () => {
  it.each([
    ["needs_clarification", ["subject", "region"], "请补充具体对象或制度名称、适用地区后重新提问。"],
    ["out_of_scope", [], "暂不支持代办、交易或执行外部操作"],
  ])("shows an actionable %s message without treating it as missing evidence", async (status, fields, message) => {
    vi.stubGlobal("fetch", vi.fn(async (url) => {
      if (String(url).endsWith("/spaces")) return jsonResponse([{ id: "a", name: "知识库" }]);
      if (String(url).includes("/ask")) return sseResponse('event: result\ndata: '+JSON.stringify({
        rag_run_id: "question-assessment", status, answer: null, verified: true,
        clarification_fields: fields, citations: [], warnings: [], retryable: false,
      })+'\n\n');
      return jsonResponse([]);
    }));
    const wrapper = mount(App);
    await flushPromises();
    await wrapper.find("textarea").setValue("它的保修期多久？");
    await button(wrapper, "从此知识库回答").trigger("click");
    await flushPromises();
    expect(wrapper.text()).toContain(message);
    expect(wrapper.text()).not.toContain("知识库未提供足够证据");
    expect(wrapper.findAll('a[href*="/sources/"]')).toHaveLength(0);
    wrapper.unmount();
  });
});

describe("selection identity regressions", () => {
  it.each([false, true])("keeps the latest citation within one answer (old failure: %s)", async (oldFails) => {
    const old = deferred(), latest = deferred();
    vi.stubGlobal("fetch", vi.fn(async (url) => {
      const path = String(url);
      if (path.endsWith("/spaces")) return jsonResponse([{ id: "a", name: "库 A" }]);
      if (path.endsWith("/sources/E1")) return old.promise;
      if (path.endsWith("/sources/E2")) return latest.promise;
      if (path.includes("/ask")) return sseResponse('event: progress\ndata: {"stage":"verified"}\n\nevent: result\ndata: '+JSON.stringify({
        rag_run_id: "same-answer", status: "answered", answer: "回答", verified: true,
        citations: ["E1", "E2"].map((id) => ({ evidence_id: id, source_url: `/api/v1/sources/${id}`, locator: {} })),
      })+'\n\n');
      return jsonResponse([]);
    }));
    const wrapper = mount(App);
    await flushPromises();
    await wrapper.find("textarea").setValue("问题");
    await button(wrapper, "从此知识库回答").trigger("click");
    await flushPromises();
    await wrapper.get('a[href$="/sources/E1"]').trigger("click");
    await wrapper.get('a[href$="/sources/E2"]').trigger("click");
    latest.resolve(jsonResponse({ text: "latest citation E2" }));
    await flushPromises();
    old.resolve(jsonResponse({ text: "stale citation E1" }, oldFails ? 503 : 200));
    await flushPromises();
    expect(wrapper.get(".source-content").text()).toContain("latest citation E2");
    expect(wrapper.text()).not.toContain("stale citation E1");
    expect(wrapper.text()).not.toContain("SOURCE_UNAVAILABLE");
    wrapper.unmount();
  });

  it("accepts the same file's pending digest after switching knowledge bases", async () => {
    const read = deferred();
    vi.stubGlobal("fetch", vi.fn(async (url) => String(url).endsWith("/spaces")
      ? jsonResponse([{ id: "a", name: "库 A" }, { id: "b", name: "库 B" }]) : jsonResponse([])));
    const wrapper = mount(App);
    await flushPromises();
    await button(wrapper, "知识库").trigger("click");
    await chooseFile(wrapper.get('[data-testid="initial-upload-file"]'), uploadFile("policy.txt", "policy", () => read.promise));
    await wrapper.get('[data-testid="global-space-select"]').setValue("b");
    await flushPromises();
    expect(wrapper.text()).toContain("正在分块计算文件哈希");
    expect(wrapper.text()).not.toContain("文件已就绪");
    expect(wrapper.get('[data-testid="initial-upload-submit"]').attributes("disabled")).toBeDefined();
    read.resolve(new TextEncoder().encode("policy").buffer);
    await vi.waitFor(() => expect(wrapper.get('[data-testid="initial-upload-hash"]').text()).toHaveLength(64));
    expect(wrapper.get('[data-testid="initial-upload-submit"]').attributes("disabled")).toBeUndefined();
    expect(wrapper.text()).toContain("文件已就绪");
    expect(wrapper.get('[data-testid="global-space-select"]').element.value).toBe("b");
    wrapper.unmount();
  });

  it.each(["cancel", "replace", "replace-error"])("ignores stale hashing after %s", async (action) => {
    const read = deferred();
    vi.stubGlobal("fetch", vi.fn(async (url) => String(url).endsWith("/spaces")
      ? jsonResponse([{ id: "a", name: "库 A" }]) : jsonResponse([])));
    const wrapper = mount(App);
    await flushPromises();
    await button(wrapper, "知识库").trigger("click");
    const input = wrapper.get('[data-testid="initial-upload-file"]');
    await chooseFile(input, uploadFile("old.txt", "old", () => read.promise));
    await chooseFile(input, action === "cancel" ? null : uploadFile("new.txt", "new"));
    if (action !== "cancel") await vi.waitFor(() => expect(wrapper.get('[data-testid="initial-upload-hash"]').text()).toHaveLength(64));
    const digest = wrapper.get('[data-testid="initial-upload-hash"]').text();
    if (action === "replace-error") read.reject(new Error("OLD_HASH_FAILURE"));
    else read.resolve(new TextEncoder().encode("old").buffer);
    await flushPromises();
    expect(wrapper.get('[data-testid="initial-upload-hash"]').text()).toBe(digest);
    expect(wrapper.text()).not.toContain("OLD_HASH_FAILURE");
    expect(wrapper.get('[data-testid="initial-upload-submit"]').attributes("disabled") !== undefined).toBe(action === "cancel");
    wrapper.unmount();
  });

  it("keeps a failed file hash failed on a space switch and recovers on a new file", async () => {
    vi.stubGlobal("fetch", vi.fn(async (url) => String(url).endsWith("/spaces")
      ? jsonResponse([{ id: "a", name: "库 A" }, { id: "b", name: "库 B" }]) : jsonResponse([])));
    const wrapper = mount(App);
    await flushPromises();
    await button(wrapper, "知识库").trigger("click");
    const input = wrapper.get('[data-testid="initial-upload-file"]');
    await chooseFile(input, uploadFile("broken.txt", "broken", async () => { throw new Error("HASH_READ_FAILED"); }));
    await vi.waitFor(() => expect(wrapper.get('[data-testid="initial-upload-error"]').text()).toBe("HASH_READ_FAILED"));
    await wrapper.get('[data-testid="global-space-select"]').setValue("b");
    await flushPromises();
    expect(wrapper.get(".workflow-status").attributes("data-phase")).toBe("FAILED");
    expect(wrapper.get('[data-testid="initial-upload-submit"]').attributes("disabled")).toBeDefined();
    await chooseFile(input, uploadFile("valid.txt", "valid"));
    await vi.waitFor(() => expect(wrapper.get('[data-testid="initial-upload-submit"]').attributes("disabled")).toBeUndefined());
    expect(wrapper.get('[data-testid="initial-upload-error"]').text()).toBe("");
    wrapper.unmount();
  });

  it.each(["stay", "switch-space", "switch-document", "stale-job-error"])("switches the displayed version and scopes ingestion completion: %s", async (action) => {
    vi.stubGlobal("crypto", webcrypto);
    const job = deferred(), oldMore = deferred();
    let completed = false;
    const document = (version) => ({ document_id: "doc", space_id: "a", version_id: version, filename: "policy.txt", processing_state: "VALIDATED" });
    const otherDocument = { document_id: "other", space_id: "a", version_id: "other-v", filename: "other.txt" };
    const chunk = (version, text) => ({ chunk_id: `${version}-chunk`, document_version_id: version, text, ordinal: 0, kind: "paragraph", locator: {} });
    vi.stubGlobal("fetch", vi.fn(async (url) => {
      const path = String(url);
      if (path.endsWith("/spaces")) return jsonResponse([{ id: "a", name: "库 A" }, { id: "b", name: "库 B" }]);
      if (path.includes("/spaces/a/documents")) return jsonResponse([document(completed ? "v2" : "v1"), otherDocument]);
      if (path.includes("/spaces/b/documents")) return jsonResponse([]);
      if (path.endsWith("/documents/doc/preview")) return jsonResponse({ row_version: 1 });
      if (path.includes("/v1/chunks?cursor=")) return oldMore.promise;
      if (path.endsWith("/v1/chunks")) return jsonResponse([chunk("v1", "version one content")], 200, { "X-Next-Cursor": "v1-cursor" });
      if (path.endsWith("/v2/chunks/preview")) return jsonResponse([chunk("v2", "version two content")]);
      if (path.endsWith("/other-v/chunks/preview")) return jsonResponse([chunk("other-v", "other document content")]);
      if (path.endsWith("/quality-report")) return jsonResponse({ parser_revision: path.includes("/document-versions/v1/") ? "quality-v1" : "quality-v2" });
      if (path.endsWith("/documents/doc/versions/upload-sessions")) return jsonResponse({ upload_session_id: "u2", upload_path: "/api/v1/upload-sessions/u2/content", row_version: 1 });
      if (path.endsWith("/upload-sessions/u2/content")) return jsonResponse({ row_version: 2 });
      if (path.endsWith("/upload-sessions/u2:complete")) { completed = true; return jsonResponse({ document_id: "doc", document_version_id: "v2", job_id: "job-v2" }); }
      if (path.endsWith("/ingestion-jobs/job-v2")) return job.promise;
      return jsonResponse([]);
    }));
    const wrapper = mount(App);
    await flushPromises();
    await button(wrapper, "知识库").trigger("click");
    await button(wrapper, "查看分块").trigger("click");
    await flushPromises();
    expect(wrapper.get('[data-testid="chunk-panel"]').text()).toContain("version one content");
    await button(wrapper, "加载更多分块").trigger("click");
    await button(wrapper, "读取 Document row version").trigger("click");
    await flushPromises();
    await chooseFile(wrapper.get('[data-testid="version-upload-file"]'), uploadFile("policy-v2.txt", "new version"));
    await vi.waitFor(() => expect(button(wrapper, "上传不可变新版本").attributes("disabled")).toBeUndefined());
    await button(wrapper, "上传不可变新版本").trigger("click");
    await flushPromises();
    const panel = wrapper.get('[data-testid="chunk-panel"]');
    expect(panel.get("h3").text()).toContain("v2");
    expect(panel.text()).not.toContain("version one content");
    expect(panel.text()).not.toContain("加载更多分块");
    expect(wrapper.text()).not.toContain("quality-v1");
    expect(wrapper.get('[data-testid="document-preview"]').element.checked).toBe(true);
    oldMore.resolve(jsonResponse([chunk("v1", "old extra page")], 200, { "X-Next-Cursor": "old-next" }));
    await flushPromises();
    expect(panel.text()).not.toContain("old extra page");
    if (action === "switch-space" || action === "stale-job-error") {
      await wrapper.get('[data-testid="global-space-select"]').setValue("b");
      await flushPromises();
    } else if (action === "switch-document") {
      await wrapper.findAll("button").filter((item) => item.text().includes("查看分块"))[1].trigger("click");
      await flushPromises();
    }
    job.resolve(action === "stale-job-error" ? jsonResponse({}, 503) : jsonResponse({ state: "SUCCEEDED", attempt: 1 }));
    await flushPromises();
    await flushPromises();
    expect(wrapper.text()).not.toContain("old extra page");
    if (action === "stay") {
      expect(wrapper.get('[data-testid="chunk-panel"]').text()).toContain("version two content");
      expect(wrapper.text()).toContain("quality-v2");
      expect(fetch.mock.calls.some(([url]) => String(url).endsWith("/v2/chunks/preview"))).toBe(true);
    } else {
      expect(wrapper.text()).not.toContain("version two content");
      expect(wrapper.text()).not.toContain("quality-v2");
      expect(wrapper.get('[data-testid="initial-upload-error"]').text()).toBe("");
      if (action === "switch-document") expect(wrapper.get('[data-testid="chunk-panel"]').text()).toContain("other document content");
    }
    expect(fetch.mock.calls.some(([url]) => String(url).includes("/v2/chunks") && String(url).includes("cursor="))).toBe(false);
    wrapper.unmount();
  });
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("trusted QA UI", () => {
  it("clears the prior library immediately while the next list is loading", async () => {
    let release;
    const pending = new Promise((resolve) => { release = resolve; });
    vi.stubGlobal("fetch", vi.fn(async (url) => {
      const path = String(url);
      if (path.endsWith("/spaces")) return jsonResponse([{ id: "a", name: "库 A" }, { id: "b", name: "库 B" }]);
      if (path.endsWith("/spaces/a/documents")) return jsonResponse([{ document_id: "a", version_id: "va", filename: "A-only.md" }]);
      if (path.endsWith("/spaces/b/documents")) return pending;
      return jsonResponse([]);
    }));
    const wrapper = mount(App);
    await flushPromises();
    await button(wrapper, "知识库").trigger("click");
    expect(wrapper.text()).toContain("A-only.md");
    await wrapper.get('[data-testid="global-space-select"]').setValue("b");
    expect(wrapper.text()).not.toContain("A-only.md");
    release(jsonResponse([]));
    await flushPromises();
  });

  it.each([false, true])("ignores delayed old-document chunks or errors (error=%s)", async (failed) => {
    let release;
    const pending = new Promise((resolve) => { release = resolve; });
    vi.stubGlobal("fetch", vi.fn(async (url) => {
      const path = String(url);
      if (path.endsWith("/spaces")) return jsonResponse([{ id: "a", name: "库 A" }]);
      if (path.endsWith("/documents")) return jsonResponse([
        { document_id: "a", version_id: "va", filename: "A.md" },
        { document_id: "b", version_id: "vb", filename: "B.md" },
      ]);
      if (path.includes("/va/chunks")) return pending;
      if (path.includes("/vb/chunks")) return jsonResponse([{ chunk_id: "cb", text: "B body", locator: {} }]);
      return jsonResponse([]);
    }));
    const wrapper = mount(App);
    await flushPromises();
    await button(wrapper, "知识库").trigger("click");
    await wrapper.findAll('[data-testid="view-chunks"]')[0].trigger("click");
    await flushPromises();
    await wrapper.findAll('[data-testid="view-chunks"]')[1].trigger("click");
    await flushPromises();
    release(failed ? jsonResponse({ code: "OLD_DOCUMENT_FAILED" }, 500)
      : jsonResponse([{ chunk_id: "ca", text: "A late body", locator: {} }]));
    await flushPromises();
    expect(wrapper.get('[data-testid="chunk-panel"]').text()).toContain("B body");
    expect(wrapper.text()).not.toContain("A late body");
    expect(wrapper.text()).not.toContain("OLD_DOCUMENT_FAILED");
  });

  it("ignores old-library answer progress/results and search results", async () => {
    let releaseAnswer, releaseSearch;
    const answer = new Promise((resolve) => { releaseAnswer = resolve; });
    const search = new Promise((resolve) => { releaseSearch = resolve; });
    vi.stubGlobal("fetch", vi.fn(async (url) => {
      const path = String(url);
      if (path.endsWith("/spaces")) return jsonResponse([{ id: "a", name: "库 A" }, { id: "b", name: "库 B" }]);
      if (path.includes("/ask")) return answer;
      if (path.endsWith("/search")) return search;
      return jsonResponse([]);
    }));
    const wrapper = mount(App);
    await flushPromises();
    await wrapper.find("textarea").setValue("A 的问题");
    await button(wrapper, "从此知识库回答").trigger("click");
    await button(wrapper, "检索调试").trigger("click");
    await button(wrapper, "在当前知识库检索").trigger("click");
    await flushPromises();
    await wrapper.get('[data-testid="global-space-select"]').setValue("b");
    releaseAnswer(sseResponse('event: progress\ndata: {"stage":"verified"}\n\nevent: result\ndata: {"status":"answered","answer":"OLD_ANSWER","citations":[],"verified":true}\n\n'));
    releaseSearch(jsonResponse({ hits: [{ text: "OLD_SEARCH" }] }));
    await flushPromises();
    expect(wrapper.text()).not.toContain("OLD_SEARCH");
    await button(wrapper, "知识问答").trigger("click");
    expect(wrapper.text()).not.toContain("OLD_ANSWER");
    expect(wrapper.text()).not.toContain("已验证");
    expect(wrapper.text()).toContain("等待输入");
  });

  it("follows a server cursor even when authorization leaves an empty page", async () => {
    vi.stubGlobal("fetch", vi.fn(async (url) => {
      const path = String(url);
      if (path.endsWith("/spaces")) return jsonResponse([{ id: "a", name: "库 A" }]);
      if (path.endsWith("/documents")) return jsonResponse([], 200, { "X-Next-Cursor": "position-1" });
      if (path.includes("cursor=position-1")) return jsonResponse([{ document_id: "b", version_id: "vb", filename: "visible.md" }]);
      return jsonResponse([]);
    }));
    const wrapper = mount(App);
    await flushPromises();
    await button(wrapper, "知识库").trigger("click");
    await button(wrapper, "加载更多文件").trigger("click");
    await flushPromises();
    expect(wrapper.text()).toContain("visible.md");
    expect(button(wrapper, "加载更多文件")).toBeUndefined();
  });

  it("shows a valid model refusal as insufficient evidence", async () => {
    vi.stubGlobal("fetch", vi.fn(async (url) => {
      const path = String(url);
      if (path.endsWith("/spaces")) return jsonResponse([{ id: "a", name: "库 A" }]);
      if (path.includes("/ask")) return sseResponse(
        'event: progress\ndata: {"stage":"verified"}\n\n' +
        'event: result\ndata: {"status":"insufficient_evidence","answer":null,"citations":[],"verified":true,"warnings":["MODEL_INSUFFICIENT_EVIDENCE"]}\n\n',
      );
      return jsonResponse([]);
    }));
    const wrapper = mount(App);
    await flushPromises();
    await wrapper.find("textarea").setValue("退款政策是什么？");
    await button(wrapper, "从此知识库回答").trigger("click");
    await flushPromises();
    expect(wrapper.find(".answer").text()).toContain("现有资料不足以回答这个问题");
    expect(wrapper.text()).not.toContain("system_error");
    expect(wrapper.text()).not.toContain("验证失败");
  });

  it.each([
    ["unavailable", "system_error", true, false, "服务暂不可用"],
    ["degraded", "answered", false, true, "部分检索服务暂不可用"],
  ])("shows retrieval health %s instead of claiming missing evidence", async (health, status, retryable, verified, message) => {
    vi.stubGlobal("fetch", vi.fn(async (url) => {
      const path = String(url);
      if (path.endsWith("/spaces")) return jsonResponse([{ id: "a", name: "库 A" }]);
      if (path.includes("/ask")) return sseResponse(
        `event: progress\ndata: ${JSON.stringify({ stage: verified ? "verified" : "verification_failed" })}\n\n` +
        `event: result\ndata: ${JSON.stringify({ status, answer: verified ? "证据回答" : null, verified, citations: [], retrieval_health: health, degraded: true, retryable })}\n\n`,
      );
      return jsonResponse([]);
    }));
    const wrapper = mount(App);
    await flushPromises();
    await wrapper.find("textarea").setValue("问题");
    await button(wrapper, "从此知识库回答").trigger("click");
    await flushPromises();
    expect(wrapper.text()).toContain(message);
    expect(wrapper.text()).not.toContain("insufficient_evidence");
    if (verified) expect(wrapper.text()).toContain("证据回答");
    else expect(wrapper.text()).not.toContain("已验证");
  });

  it("ignores a delayed old-library document response", async () => {
    let release;
    const pending = new Promise((resolve) => { release = resolve; });
    vi.stubGlobal("fetch", vi.fn(async (url) => {
      const path = String(url);
      if (path.endsWith("/spaces")) return jsonResponse([{ id: "a", name: "库 A" }, { id: "b", name: "库 B" }]);
      if (path.endsWith("/spaces/a/documents")) return pending;
      if (path.endsWith("/spaces/b/documents")) return jsonResponse([{ document_id: "b", version_id: "vb", filename: "B-only.md" }]);
      return jsonResponse({});
    }));
    const wrapper = mount(App);
    await flushPromises();
    await wrapper.get('[data-testid="global-space-select"]').setValue("b");
    await flushPromises();
    release(jsonResponse([{ document_id: "a", version_id: "va", filename: "A-secret.md" }]));
    await flushPromises();
    await button(wrapper, "知识库").trigger("click");
    expect(wrapper.text()).toContain("B-only.md");
    expect(wrapper.text()).not.toContain("A-secret.md");
  });

  it("loads signed citations through the authenticated API, without token in URL", async () => {
    const calls = [];
    vi.stubGlobal("fetch", vi.fn(async (url, options = {}) => {
      calls.push([String(url), options]);
      if (String(url).endsWith("/spaces")) return jsonResponse([{ id: "a", name: "库 A" }]);
      if (String(url).includes("/sources/")) return jsonResponse({ text: "authorized source evidence" });
      if (String(url).includes("/ask")) return sseResponse('event: progress\ndata: {"stage":"verified"}\n\nevent: result\ndata: {"status":"answered","answer":"证据回答","verified":true,"citations":[{"evidence_id":"E1","source_url":"/api/v1/sources/ref?signature=synthetic","locator":{}}]}\n\n');
      return jsonResponse([]);
    }));
    const wrapper = mount(App);
    await flushPromises();
    await wrapper.find("textarea").setValue("问题");
    await button(wrapper, "从此知识库回答").trigger("click");
    await flushPromises();
    await wrapper.find('a[href*="/sources/"]').trigger("click");
    await flushPromises();
    const [url, options] = calls.find(([path]) => path.includes("/sources/"));
    expect(new Headers(options.headers).get("Authorization")).toBe("Bearer synthetic-browser-token");
    expect(url).not.toContain("synthetic-browser-token");
    expect(wrapper.text()).toContain("authorized source evidence");
  });
  it("shows an answer only after verified progress", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url) =>
        String(url).endsWith("/spaces")
          ? jsonResponse([{ id: "space-1", name: "产品知识库", status: "ACTIVE" }])
          : String(url).endsWith("/spaces/space-1/documents")
            ? jsonResponse([])
            : sseResponse(
          'event: progress\ndata: {"stage":"verified"}\n\n' +
            'event: result\ndata: {"status":"answered","answer":"三年",' +
            '"citations":[],"verified":true}\n\n',
        ),
      ),
    );
    const wrapper = mount(App);
    await flushPromises();
    await wrapper.find("textarea").setValue("保修期多久？");
    await button(wrapper, "从此知识库回答").trigger("click");
    await flushPromises();

    expect(wrapper.text()).toContain("三年");
    expect(wrapper.text()).toContain("已验证");
  });

  it("renders a stable error and no answer when streaming fails", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url) =>
        String(url).endsWith("/spaces")
          ? jsonResponse([{ id: "space-1", name: "产品知识库", status: "ACTIVE" }])
          : String(url).endsWith("/spaces/space-1/documents")
            ? jsonResponse([])
            : new Response("{}", { status: 503 }),
      ),
    );
    const wrapper = mount(App);
    await flushPromises();
    await wrapper.find("textarea").setValue("问题");
    await button(wrapper, "从此知识库回答").trigger("click");
    await flushPromises();

    expect(wrapper.text()).toContain("SSE_REQUEST_FAILED");
    expect(wrapper.text()).toContain("system_error");
    expect(wrapper.text()).toContain("答案仅在引用与权限复核后显示");
  });

  it("uploads an initial document and exposes its indexing job", async () => {
    vi.stubGlobal("crypto", webcrypto);
    let documentReads = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url, options = {}) => {
        const path = String(url);
        if (path.endsWith("/spaces")) {
          return jsonResponse([{ id: "space-1", name: "制度库", status: "ACTIVE" }]);
        }
        if (path.endsWith("/spaces/space-1/documents") || path.endsWith("/spaces/space-1/documents/preview")) {
          documentReads += 1;
          return jsonResponse(
            documentReads === 1
              ? []
              : [{
                  document_id: "document-1",
                  space_id: "space-1",
                  filename: "policy.md",
                  version_id: "version-1",
                  version_no: 1,
                  processing_state: "VALIDATED",
                  publication_state: "STAGED",
                  parser_revision: "parser:v1",
                  chunk_count: 1,
                  job_id: "job-1",
                }],
          );
        }
        if (path.endsWith("/spaces/space-1/upload-sessions")) {
          return jsonResponse({ upload_session_id: "upload-1", upload_path: "/api/v1/upload-sessions/upload-1/content", row_version: 1 });
        }
        if (path.endsWith("/upload-sessions/upload-1/content")) {
          return jsonResponse({ row_version: 2 });
        }
        if (path.endsWith("/upload-sessions/upload-1:complete")) {
          return jsonResponse({ document_id: "document-1", document_version_id: "version-1", job_id: "job-1" });
        }
        if (path.endsWith("/ingestion-jobs/job-1")) {
          return jsonResponse({ id: "job-1", operation: "process_document", state: "SUCCEEDED", attempt: 1, max_attempts: 3, cancel_requested: false, error_code: null });
        }
        if (path.endsWith("/document-versions/version-1/quality-report")) {
          return jsonResponse({ document_version_id: "version-1", source_format: "md", parser_revision: "parser:v1", node_count: 1, locator_coverage: 1, issue_codes: [], disposition: "PASS", real_acceptance: false });
        }
        if (path.endsWith("/document-versions/version-1/chunks/preview")) {
          return jsonResponse([{ chunk_id: "chunk-1", document_version_id: "version-1", parent_chunk_id: null, ordinal: 0, kind: "paragraph", token_count: 3, status: "STAGED", text: "policy content", locator: { line_start: 1 } }]);
        }
        return jsonResponse({ method: options.method }, 404);
      }),
    );
    const wrapper = mount(App);
    await flushPromises();
    await button(wrapper, "知识库").trigger("click");
    const input = wrapper.get('[data-testid="initial-upload-file"]');
    const file = new File(["policy"], "policy.md", { type: "text/markdown" });
    Object.defineProperty(file, "arrayBuffer", {
      value: async () => {
        throw new Error("whole file read is forbidden");
      },
    });
    Object.defineProperty(file, "slice", {
      value: (start, end) => ({
        arrayBuffer: async () => new TextEncoder().encode("policy").slice(start, end).buffer,
      }),
    });
    Object.defineProperty(input.element, "files", { value: [file] });
    await input.trigger("change");
    await vi.waitFor(() => {
      expect(wrapper.get('[data-testid="initial-upload-hash"]').text()).toHaveLength(64);
    });
    expect(wrapper.get('[data-testid="initial-upload-submit"]').attributes("disabled")).toBeUndefined();
    await wrapper.get('[data-testid="initial-upload-submit"]').trigger("click");
    await flushPromises();
    await flushPromises();

    expect(wrapper.get('[data-testid="initial-upload-error"]').text()).toBe("");
    expect(wrapper.get('[data-testid="initial-upload-result"]').text()).toContain("version-1");
    expect(wrapper.text()).toContain("解析入库完成");
    expect(wrapper.get('[data-testid="document-list"]').text()).toContain("policy.md");
    expect(wrapper.get('[data-testid="chunk-panel"]').text()).toContain("policy content");
    expect(wrapper.get('[data-testid="document-preview"]').element.checked).toBe(true);
    expect(fetch.mock.calls.some(([url]) => String(url).endsWith("/document-versions/version-1/chunks/preview"))).toBe(true);
  });

  it("creates and selects a knowledge base", async () => {
    vi.stubGlobal("crypto", webcrypto);
    let created = false;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url, options = {}) => {
        const path = String(url);
        if (path.endsWith("/spaces") && options.method === "POST") {
          created = true;
          return jsonResponse({ id: "space-new", tenant_id: "local", name: "产品手册", status: "ACTIVE" }, 201);
        }
        if (path.endsWith("/spaces")) {
          return jsonResponse(created ? [{ id: "space-new", name: "产品手册", status: "ACTIVE" }] : []);
        }
        if (path.endsWith("/spaces/space-new/documents")) return jsonResponse([]);
        return jsonResponse({}, 404);
      }),
    );
    const wrapper = mount(App);
    await flushPromises();
    await button(wrapper, "知识库").trigger("click");
    await wrapper.get('[data-testid="new-space-name"]').setValue("产品手册");
    await wrapper.get('[data-testid="create-space-submit"]').trigger("click");
    await flushPromises();

    expect(wrapper.get('[data-testid="space-list"]').text()).toContain("产品手册");
    expect(wrapper.get('[data-testid="global-space-select"]').element.value).toBe("space-new");
  });
});
