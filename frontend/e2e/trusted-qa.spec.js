import { expect, test } from "@playwright/test";
import { execFileSync } from "node:child_process";
import { createHash } from "node:crypto";
import { writeFileSync } from "node:fs";

test("ask progress releases the verified answer and citation", async ({ page }) => {
  await page.route("**/api/v1/ask:stream", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "text/event-stream",
      body:
        'event: progress\ndata: {"stage":"retrieval_started"}\n\n' +
        'event: progress\ndata: {"stage":"verified"}\n\n' +
        'event: result\ndata: {"rag_run_id":"run-1","status":"answered",' +
        '"answer":"保修期为三年。","citations":[{"evidence_id":"E1",' +
        '"source_url":"/api/v1/source"}],"verified":true}\n\n',
    });
  });
  await page.goto("/");
  await page.locator("textarea").fill("保修期多久？");
  await page.getByRole("button", { name: "从此知识库回答" }).click();

  await expect(page.getByText("保修期为三年。")).toBeVisible();
  await expect(page.getByRole("link", { name: /E1/ })).toBeVisible();
});

test("upload, worker indexing, publish, ask, and citation use the real local backend", async ({
  page,
}, testInfo) => {
  const runtimeConfig = await page.request.get("http://127.0.0.1:4173/runtime-config.js");
  expect(runtimeConfig.ok()).toBeTruthy();
  expect(await runtimeConfig.text()).toContain("http://127.0.0.1:8000/api/v1");
  const policy = testInfo.outputPath("policy.md");
  const content = Buffer.from("# Product policy\nWarranty is three years.\n", "utf8");
  writeFileSync(policy, content);
  await page.goto("/");
  await page.getByRole("button", { name: "知识库", exact: true }).click();
  await page.getByTestId("new-space-name").fill(`Playwright 产品库 ${Date.now()}`);
  await page.getByTestId("create-space-submit").click();
  await page.getByTestId("initial-upload-file").setInputFiles(policy);
  await expect(page.getByTestId("initial-upload-hash")).toHaveText(
    createHash("sha256").update(content).digest("hex"),
  );
  await page.getByTestId("initial-upload-submit").click();
  await expect(page.getByTestId("initial-upload-result")).toContainText(/Job ID.*01a/);

  const workerOutput = execFileSync(
    process.env.RAGKB_E2E_WORKER,
    ["--once"],
    {
      cwd: process.cwd(),
      env: process.env,
      stdio: "pipe",
    },
  ).toString();
  expect(workerOutput).toContain('"failed": false');
  await expect(page.getByText("解析入库完成", { exact: false })).toBeVisible();
  await expect(page.getByTestId("document-list")).toContainText("policy.md");
  await page.getByTestId("view-chunks").click();
  await expect(page.getByTestId("chunk-panel")).toContainText("Warranty is three years.");
  await page.getByPlaceholder("复核说明").fill("browser e2e");
  await page.getByRole("button", { name: "提交复核", exact: true }).click();
  await page.getByRole("button", { name: "发布文档", exact: true }).click();
  await expect(page.getByTestId("document-list")).toContainText("SERVING");

  await page.getByRole("button", { name: "知识问答" }).click();
  await page.locator("textarea").fill("What is the warranty period?");
  await page.getByRole("button", { name: "从此知识库回答" }).click();
  await expect(page.getByText("根据已验证证据，设备保修期为三年。")).toBeVisible();
  await expect(page.getByRole("link", { name: /E1/ })).toBeVisible();
});
