import { flushPromises, mount } from "@vue/test-utils";
import { afterEach, describe, expect, it, vi } from "vitest";

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
});
