import { flushPromises, mount } from "@vue/test-utils";
import { afterEach, describe, expect, it, vi } from "vitest";
import { webcrypto } from "node:crypto";

import App from "./App.vue";

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

function jsonResponse(payload, status = 200) {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("trusted QA UI", () => {
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
        if (path.endsWith("/spaces/space-1/documents")) {
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
        if (path.endsWith("/document-versions/version-1/chunks")) {
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
