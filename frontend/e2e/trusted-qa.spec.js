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
  await page.getByRole("button", { name: "提交可信问答" }).click();

  await expect(page.getByText("保修期为三年。")).toBeVisible();
  await expect(page.getByRole("link", { name: /E1/ })).toBeVisible();
});

test("upload, worker indexing, publish, ask, and citation use the real local backend", async ({
  page,
}, testInfo) => {
  const policy = testInfo.outputPath("policy.md");
  const content = Buffer.from("# Product policy\nWarranty is three years.\n", "utf8");
  writeFileSync(policy, content);
  const spaces = await page.request.get("http://127.0.0.1:8000/api/v1/spaces");
  const spacesBody = await spaces.json();
  expect(spaces.ok(), JSON.stringify(spacesBody)).toBeTruthy();
  const spaceId = spacesBody[0].id;
  const createdResponse = await page.request.post(
    `http://127.0.0.1:8000/api/v1/spaces/${spaceId}/upload-sessions`,
    {
      headers: { "Idempotency-Key": `e2e-create-${Date.now()}` },
      data: {
        filename: "policy.md",
        expected_size: content.length,
        expected_sha256: createHash("sha256").update(content).digest("hex"),
        declared_mime: "text/markdown",
      },
    },
  );
  const created = await createdResponse.json();
  expect(createdResponse.ok(), JSON.stringify(created)).toBeTruthy();
  const uploadedResponse = await page.request.put(
    `http://127.0.0.1:8000${created.upload_path}`,
    { headers: { "If-Match": `"${created.row_version}"` }, data: content },
  );
  const uploaded = await uploadedResponse.json();
  expect(uploadedResponse.ok(), JSON.stringify(uploaded)).toBeTruthy();
  const completedResponse = await page.request.post(
    `http://127.0.0.1:8000/api/v1/upload-sessions/${created.upload_session_id}:complete`,
    {
      headers: {
        "If-Match": `"${uploaded.row_version}"`,
        "Idempotency-Key": `e2e-complete-${Date.now()}`,
      },
    },
  );
  const completed = await completedResponse.json();
  expect(completedResponse.ok(), JSON.stringify(completed)).toBeTruthy();
  await page.goto("/");
  await page.getByRole("button", { name: "知识管理" }).click();
  await page.getByPlaceholder("Document ID").fill(completed.document_id);
  await page.getByPlaceholder("Version ID").fill(completed.document_version_id);

  const workerOutput = execFileSync(
    process.env.RAGKB_E2E_PYTHON,
    ["../run_worker.py", "--once"],
    {
    cwd: process.cwd(),
    env: process.env,
    stdio: "pipe",
    },
  ).toString();
  expect(workerOutput).toContain('"failed": false');
  const reviewed = await page.request.post(
    `http://127.0.0.1:8000/api/v1/document-versions/${completed.document_version_id}/review`,
    {
      headers: { "Idempotency-Key": `e2e-review-${Date.now()}` },
      data: {
        decision: "APPROVED",
        comment: "browser e2e",
        security_projection: {
          visibility: "TENANT",
          classification_level: 0,
          acl_scope_tokens: [],
        },
      },
    },
  );
  expect(reviewed.ok(), await reviewed.text()).toBeTruthy();
  const published = await page.request.post(
    `http://127.0.0.1:8000/api/v1/document-versions/${completed.document_version_id}:publish`,
    { headers: { "Idempotency-Key": `e2e-publish-${Date.now()}` } },
  );
  expect(published.ok(), await published.text()).toBeTruthy();

  await page.getByRole("button", { name: "可信问答" }).click();
  await page.locator("textarea").fill("What is the warranty period?");
  await page.getByRole("button", { name: "提交可信问答" }).click();
  await expect(page.getByText("根据已验证证据，设备保修期为三年。")).toBeVisible();
  await expect(page.getByRole("link", { name: /E1/ })).toBeVisible();
});
