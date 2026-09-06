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
