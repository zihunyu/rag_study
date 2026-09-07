import { expect, test } from '@playwright/test';
import { execFileSync } from 'node:child_process';
import { writeFileSync } from 'node:fs';

test.setTimeout(120000);
test.describe.configure({ mode: 'serial' });
function worker() {
  const output = execFileSync(process.env.RAGKB_E2E_WORKER, ['--once'], { cwd: process.cwd(), env: process.env, stdio: 'pipe' }).toString();
  expect(output).toContain('"failed": false');
}
async function createSpace(page, name) {
  await page.goto('/'); await page.getByRole('button', { name: '创建知识库', exact: true }).first().click();
  await page.getByLabel('知识库名称').fill(name); await page.getByRole('button', { name: '创建知识库', exact: true }).last().click();
  await expect(page).toHaveURL(/knowledge-bases\/[^/]+\/documents/); return page.url().split('/').at(-2);
}
async function publish(page) {
  await page.getByRole('button', { name: '确认发布', exact: true }).first().click();
  await page.getByRole('dialog', { name: '确认发布', exact: true }).getByRole('button', { name: '确认发布', exact: true }).click();
  await expect(page.locator('.document-status-strip')).toContainText('可问答');
}

test('management upload, quality, publication, multi-turn citations and version rollback', async ({ page }, testInfo) => {
  const space = await createSpace(page, `Playwright 工作台 ${Date.now()}`);
  const policy = testInfo.outputPath('policy.md'), second = testInfo.outputPath('notes.md'), invalid = testInfo.outputPath('unsupported.exe');
  writeFileSync(policy, '# 星云 X1 产品政策\n星云 X1 的保修期为三年。星云 X1 支持上门维修服务。\n');
  writeFileSync(second, '# 资料维护\n知识库资料由产品团队定期核对更新。\n'); writeFileSync(invalid, 'invalid format');
  await page.getByRole('button', { name: '上传文档', exact: true }).first().click();
  await page.getByTestId('upload-files').setInputFiles([policy, second, invalid]);
  await expect(page.locator('.upload-row').filter({ hasText: 'policy.md' })).toContainText('已提交处理');
  await expect(page.locator('.upload-row').filter({ hasText: 'notes.md' })).toContainText('已提交处理');
  await expect(page.locator('.upload-row').filter({ hasText: 'unsupported.exe' })).toContainText('不支持这个文件类型');
  worker(); worker();
  await page.locator('.upload-row').filter({ hasText: 'policy.md' }).getByRole('link', { name: '查看已上传文档' }).click();
  await expect(page.getByRole('heading', { name: '发布前，检查你的知识' })).toBeVisible();
  await expect(page.locator('.quality-metrics')).toContainText('来源位置覆盖率');
  await page.getByRole('button', { name: '阅读解析内容' }).click();
  await expect(page.locator('.content-chunk').first()).toContainText('星云 X1');
  await page.getByRole('button', { name: '质量检查', exact: true }).click(); await publish(page);
  const documentUrl = page.url();
  await page.goto(`/chat?space=${space}`);
  await page.getByRole('textbox', { name: '向知识库提问' }).fill('星云 X1 保修多久？'); await page.getByRole('button', { name: '发送问题' }).click();
  await expect(page.locator('.assistant-body .markdown-content').first()).toContainText('三年');
  await page.getByRole('textbox', { name: '向知识库提问' }).fill('它支持上门维修吗？'); await page.getByRole('button', { name: '发送问题' }).click();
  await expect(page.locator('.assistant-label')).toHaveCount(2); await expect(page.locator('.answer-processing')).toHaveCount(0);
  await expect(page.locator('.resolved-question')).toContainText('星云 X1');
  await page.locator('.citation-button').first().click(); await expect(page.locator('.source-text')).toContainText('三年');
  await page.screenshot({path:testInfo.outputPath('conversation-with-source.png'),fullPage:true});
  await page.getByRole('button', { name: '关闭来源' }).click();
  const conversationUrl = page.url(); await page.reload(); await expect(page.locator('.conversation-turn')).toHaveCount(2);
  await page.goto(documentUrl);
  const newPolicy = testInfo.outputPath('policy-v2.md'); writeFileSync(newPolicy, '# 星云 X1 更新政策\n星云 X1 的保修期为五年。星云 X1 支持上门维修服务。\n');
  await page.getByRole('button', { name: '文档操作' }).click(); await page.getByRole('button', { name: '上传新版本', exact: true }).click();
  await page.getByTestId('upload-files').setInputFiles(newPolicy);
  await expect(page.locator('.upload-row').filter({ hasText: 'policy-v2.md' })).toContainText('已提交处理'); worker();
  await page.getByRole('button', { name: '完成', exact: true }).click(); await page.reload();
  await expect(page.getByRole('combobox', { name: '文档版本' })).toContainText('版本 2'); await publish(page);
  await page.getByRole('button', { name: '版本历史', exact: true }).click();
  await page.locator('.version-row').filter({ hasText: '版本 1' }).getByRole('button', { name: '回滚', exact: true }).click();
  await page.getByRole('dialog', { name: '回滚到此版本' }).getByRole('button', { name: '回滚到此版本', exact: true }).click();
  await expect(page.locator('.document-status-strip')).toContainText('可问答');
  await page.getByRole('button', { name: '文档操作' }).click(); await page.getByRole('button', { name: '撤回文档', exact: true }).click();
  await page.getByRole('dialog', { name: '撤回文档' }).getByRole('button', { name: '撤回文档', exact: true }).click();
  await expect(page.locator('.document-status-strip')).toContainText('已撤回');
  await page.goto(conversationUrl); await expect(page.locator('.assistant-body .markdown-content')).toHaveCount(0);
  await expect(page.locator('.answer-notice').first()).toContainText('来源已撤回');
});

test('server task survives reload and can be cancelled and retried', async ({ page }, testInfo) => {
  await createSpace(page, `Playwright 任务 ${Date.now()}`);
  const document = testInfo.outputPath('queue-test.md'); writeFileSync(document, '# 任务中心测试\n这份文档用于验证任务取消与重试。\n');
  await page.getByRole('button', { name: '上传文档', exact: true }).first().click(); await page.getByTestId('upload-files').setInputFiles(document);
  await expect(page.locator('.upload-row').filter({ hasText: 'queue-test.md' })).toContainText('已提交处理');
  await page.goto('/tasks'); await page.reload();
  const task = page.locator('.task-card').filter({ hasText: 'queue-test.md' }); await expect(task).toContainText('等待处理');
  await task.getByRole('button', { name: '取消', exact: true }).click(); await expect(task).toContainText('已取消');
  await task.getByRole('button', { name: '重试', exact: true }).click(); await expect(task).toContainText('等待处理');
});

for (const width of [390, 1024, 1440]) {
  test(`responsive workspace stays usable at ${width}px`, async ({ page }, testInfo) => {
    await page.setViewportSize({ width, height: 900 }); await page.goto('/');
    await expect(page.getByRole('heading', { name: '你的知识，从这里连接。' })).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await page.screenshot({path:testInfo.outputPath(`workspace-${width}.png`),fullPage:true});
    await page.getByRole('button', { name: '创建知识库', exact: true }).first().click(); await expect(page.getByRole('dialog', { name: '创建知识库', exact: true })).toBeVisible();
    await page.getByRole('button', { name: '取消', exact: true }).click(); await page.goto('/chat');
    await expect(page.getByRole('textbox', { name: '向知识库提问' })).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  });
}
