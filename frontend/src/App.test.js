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

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("trusted QA UI", () => {
  it("shows an answer only after verified progress", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        sseResponse(
          'event: progress\ndata: {"stage":"verified"}\n\n' +
            'event: result\ndata: {"status":"answered","answer":"三年",' +
            '"citations":[],"verified":true}\n\n',
        ),
      ),
    );
    const wrapper = mount(App);
    await wrapper.find("textarea").setValue("保修期多久？");
    await button(wrapper, "提交可信问答").trigger("click");
    await flushPromises();

    expect(wrapper.text()).toContain("三年");
    expect(wrapper.text()).toContain("已验证");
  });

  it("renders a stable error and no answer when streaming fails", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("{}", { status: 503 })));
    const wrapper = mount(App);
    await wrapper.find("textarea").setValue("问题");
    await button(wrapper, "提交可信问答").trigger("click");
    await flushPromises();

    expect(wrapper.text()).toContain("SSE_REQUEST_FAILED");
    expect(wrapper.text()).toContain("system_error");
    expect(wrapper.text()).toContain("答案仅在引用与权限复核后显示");
  });

  it("uploads an initial document and exposes its indexing job", async () => {
    vi.stubGlobal("crypto", webcrypto);
    const responses = [
      [{ id: "space-1" }],
      { upload_session_id: "upload-1", upload_path: "/api/v1/upload-sessions/upload-1/content", row_version: 1 },
      { row_version: 2 },
      { document_id: "document-1", document_version_id: "version-1", job_id: "job-1" },
    ];
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        new Response(JSON.stringify(responses.shift()), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
      ),
    );
    const wrapper = mount(App);
    await button(wrapper, "知识管理").trigger("click");
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
  });
});
