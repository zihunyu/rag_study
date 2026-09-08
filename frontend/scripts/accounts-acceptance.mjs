import { chromium, expect } from '@playwright/test';
import { readFile, writeFile, mkdir } from 'node:fs/promises';
import path from 'node:path';

const output = path.resolve('../artifacts/reviews/20260908-account-access');
await mkdir(output, { recursive: true });
const fixture = JSON.parse(await readFile(path.join(output, 'fixture.json'), 'utf8'));
const base = 'http://127.0.0.1:5173', api = 'http://127.0.0.1:8002/api';
const password = 'Fixture-only password 2026!';
const browser = await chromium.launch({ channel: 'chrome', headless: true });
const issues = [], checks = [], contexts = [];
async function context() {
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
  contexts.push(ctx);
  await ctx.addInitScript(value => { globalThis.__RAGKB_CONFIG__ = { apiBaseUrl: value }; }, api);
  await ctx.route('**/runtime-config.js', route => route.fulfill({ contentType: 'text/javascript', body: `globalThis.__RAGKB_CONFIG__ = ${JSON.stringify({ apiBaseUrl: api })};` }));
  const page = await ctx.newPage();
  page.on('pageerror', e => issues.push(e.message));
  return page;
}
async function call(page, endpoint, method = 'GET', body, headers = {}) {
  return page.evaluate(async ({ api, endpoint, method, body, headers }) => {
    const csrf = await fetch(api + '/auth/csrf', { credentials: 'include' }).then(r => r.json());
    const response = await fetch(api + endpoint, { method, credentials: 'include',
      headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf.csrf_token, ...headers },
      ...(body === undefined ? {} : { body: JSON.stringify(body) }) });
    return { status: response.status, data: await response.json(), etag: response.headers.get('ETag') };
  }, { api, endpoint, method, body, headers });
}
async function login(page, username, secret = password) {
  await page.goto(base + '/login');
  await page.getByLabel('账号', { exact: true }).fill(username);
  await page.getByLabel('密码', { exact: true }).fill(secret);
  await page.getByRole('button', { name: '登录', exact: true }).click();
  await expect(page).not.toHaveURL(/\/login/);
}
async function screenshot(page, name, width) {
  await page.setViewportSize({ width, height: width === 390 ? 844 : 1000 });
  await page.screenshot({ path: path.join(output, `${name}-${width}.png`), fullPage: true });
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1);
  if (overflow) throw new Error(`Horizontal page overflow: ${name} ${width}`);
  checks.push(`${name} ${width}px`);
}
try {
  const admin = await context(), manager = await context(), reader = await context();
  await admin.goto(base + '/login');
  await expect(admin.getByRole('heading', { name: '登录知识工作台' })).toBeVisible();
  for (const width of [390, 1024, 1440]) await screenshot(admin, 'login', width);
  await admin.getByLabel('账号', { exact: true }).fill('admin');
  await admin.getByLabel('密码', { exact: true }).fill('incorrect');
  await admin.getByRole('button', { name: '登录', exact: true }).click();
  await expect(admin.getByRole('alert')).toContainText('账号、密码或账号状态无效');
  await login(admin, 'admin');
  await expect(admin.locator('nav[aria-label="主导航"] a')).toHaveCount(6);
  checks.push('总管理员登录、统一错误提示、六项导航');
  const a = fixture.space_id;
  const b = (await call(admin, '/spaces', 'POST', { name: '验收 B · 产品知识' })).data.id;
  const c = (await call(admin, '/spaces', 'POST', { name: '验收 C · 隔离资料' })).data.id;
  await admin.goto(base + '/admin/users');
  const users = {};
  for (const [name, display] of [['manager', '知识库管理员'], ['reader', '问答用户']]) {
    await admin.getByRole('button', { name: '创建账号', exact: true }).click();
    await admin.locator('#account-username').fill(name);
    await admin.locator('#account-name').fill(display);
    await admin.getByRole('button', { name: '保存账号', exact: true }).click();
    await expect(admin.locator('.temporary-password')).toBeVisible();
    users[name] = { password: await admin.locator('.temporary-password').innerText() };
    await admin.getByRole('button', { name: '已记录，关闭' }).click();
    const row = admin.locator('tbody tr').filter({ has: admin.getByText(name, { exact: true }) });
    await row.getByRole('button', { name: '分配知识库' }).click();
    const options = admin.locator('.account-grant-row');
    await expect(options).toHaveCount(3);
    await options.first().locator('select').selectOption('none');
    const names = await options.locator('strong').allTextContents();
    for (const title of names) {
      const select = admin.getByRole('combobox', { name: title + '权限', exact: true });
      await select.selectOption(title.startsWith('验收 B') ? 'qa' : title.startsWith('验收 C') ? 'none' : name === 'manager' ? 'manager' : 'qa');
    }
    await expect(admin.locator('.permission-diff')).toBeVisible();
    if (name === 'manager') for (const width of [390, 1024, 1440]) await screenshot(admin, 'assignment', width);
    await admin.getByRole('button', { name: '确认分配' }).click();
    await expect(admin.getByRole('button', { name: '确认分配' })).toBeDisabled();
    await admin.getByRole('dialog').getByRole('button', { name: '关闭', exact: true }).click();
    users[name].id = (await call(admin, '/admin/users?q=' + name)).data.items[0].id;
  }
  checks.push('创建账号、一次性临时密码、逐库授权差异确认');
  for (const [name, page] of [['manager', manager], ['reader', reader]]) {
    await login(page, name, users[name].password);
    await expect(page).toHaveURL(/change-password/);
    await page.locator('#current-password').fill(users[name].password);
    await page.locator('#new-password').fill(password);
    await page.locator('#confirm-password').fill(password);
    await page.getByRole('button', { name: '保存新密码' }).click();
    await expect(page).toHaveURL(/login/);
    await login(page, name);
    await expect(page.locator('nav[aria-label="主导航"] a')).toHaveCount(name === 'reader' ? 2 : 3);
  }
  await expect(manager).toHaveURL(new RegExp(a + '/documents'));
  await expect(reader).toHaveURL(/\/chat$/);
  await expect(reader.locator('#chat-space option')).toHaveCount(3);
  await expect(reader.getByText('尚未分配知识库，请联系管理员。')).toHaveCount(0);
  await expect(reader.getByText('这个知识库还没有可问答资料，请联系知识库管理员。')).toBeVisible();
  await expect(reader.locator('.chat-welcome a[href*="/documents"]')).toHaveCount(0);
  for (const width of [390, 1024, 1440]) await screenshot(reader, 'reader-chat', width);
  checks.push('首次改密、三类角色首页与导航、三个浏览器上下文隔离');
  for (const [page, library, status] of [[manager, a, 200], [manager, b, 404], [manager, c, 404], [reader, a, 404]]) {
    const response = await call(page, `/spaces/${library}/documents/preview`);
    if (response.status !== status) throw new Error(`Scoped documents returned ${response.status}, expected ${status}`);
  }
  await reader.goto(base + '/admin/users');
  await expect(reader).toHaveURL(/\/chat/);
  await manager.goto(base + '/system');
  await expect(manager).not.toHaveURL(/\/system/);
  await manager.goto(base + `/knowledge-bases/${a}/members`);
  await expect(manager.getByRole('heading', { name: '知识库成员' })).toBeVisible();
  await expect(manager.locator('.account-panel tbody tr')).toHaveCount(2);
  await expect(manager.getByRole('heading', { name: '验收 A · 团队手册', exact: true })).toBeVisible();
  for (const width of [390, 1024, 1440]) await screenshot(manager, 'members', width);
  await manager.goto(base + `/knowledge-bases/${a}/activity`);
  await expect(manager.getByRole('heading', { name: '本库用量' })).toBeVisible();
  checks.push('管理 A、问答 B、禁止 C；直接改地址被拦截；本库成员和用量');
  const conversation = await call(reader, '/conversations', 'POST', { space_id: a, title: '权限撤销验收' }, { 'Idempotency-Key': crypto.randomUUID() });
  await reader.goto(base + '/chat/' + conversation.data.id);
  await expect(reader.getByText('权限撤销验收').first()).toBeVisible();
  const membership = await call(manager, `/spaces/${a}/members`);
  const revoke = await call(manager, `/spaces/${a}/members`, 'PUT', { changes: [{ user_id: users.reader.id, role: 'none' }] }, { 'If-Match': membership.etag });
  if (revoke.status !== 200) throw new Error('Manager failed to revoke QA member');
  await reader.evaluate(() => window.dispatchEvent(new Event('focus')));
  await expect(reader.getByText('权限撤销验收')).toHaveCount(0);
  if ((await call(reader, '/conversations/' + conversation.data.id)).status !== 404) throw new Error('Revoked history visible');
  await admin.goto(base + '/admin/audit');
  await admin.getByRole('button', { name: '问答审计', exact: true }).click();
  await admin.getByRole('button', { name: '查看问答', exact: true }).first().click();
  await admin.locator('#audit-reason').fill('隔离验收：验证授权审计记录');
  await admin.getByRole('button', { name: '记录原因并查看' }).click();
  await expect(admin.getByRole('heading', { name: '权限撤销验收' })).toBeVisible();
  checks.push('管理员仅撤销问答成员、焦点刷新撤权、旧会话隐藏、独立问答审计');
  const state = await call(manager, `/spaces/${a}/members`);
  if ((await call(manager, `/spaces/${a}:delete`, 'POST', undefined, { 'If-Match': state.etag, 'Idempotency-Key': crypto.randomUUID() })).status !== 200) throw new Error('Soft delete failed');
  await manager.reload();
  await manager.goto(base + '/knowledge-bases');
  await manager.getByRole('button', { name: '知识库回收站' }).click();
  await manager.getByRole('button', { name: '恢复知识库', exact: true }).click();
  await expect(manager.getByRole('dialog')).toHaveCount(0);
  await manager.getByRole('button', { name: '知识库回收站' }).click();
  await expect(manager.getByText('没有可恢复的知识库。')).toBeVisible();
  checks.push('库管理员软删除及回收站恢复');
  await reader.locator('.account-trigger').click();
  await reader.getByRole('button', { name: '退出登录', exact: true }).click();
  await expect(reader).toHaveURL(/login/);
  await reader.reload();
  await expect(reader.getByRole('heading', { name: '登录知识工作台' })).toBeVisible();
  if ((await call(reader, '/spaces')).status !== 401) throw new Error('Logout session still active');
  checks.push('退出后刷新保持未登录');
  if (issues.length) throw new Error('Browser errors: ' + issues.join('; '));
  await writeFile(path.join(output, 'browser-report.json'), JSON.stringify({ passed: true, checks, issues }, null, 2));
  console.log(JSON.stringify({ passed: true, checks: checks.length, screenshots: 12 }));
} catch (error) {
  await writeFile(path.join(output, 'browser-report.json'), JSON.stringify({ passed: false, checks, error: error.message, issues }, null, 2));
  throw error;
} finally { await browser.close(); }
