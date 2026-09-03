import { expect, test } from "@playwright/test";

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
