/**
 * 端到端测试共用装置。
 *
 * 直接测试「同源单端口」运行形态：FastAPI 同时提供 /api/* 与前端 build。
 * 通过 API 请求上下文（真实 HTTP + Cookie）创建固定算例数据，
 * 再用真实浏览器验证界面，保证测试跑的就是生产形态。
 */

import { expect, type APIRequestContext, type Locator, type Page } from '@playwright/test';

export const PASSWORD = 'Wendai2025';

/**
 * 按文案定位按钮，兼容 Ant Design 的渲染细节。
 *
 * * 相邻汉字之间会插入空格（"登录" 渲染为 "登 录"）
 * * 中文与拉丁字母之间会留出空格（"导入 CSV" 渲染为 "导 入 CSV"）
 * * 带图标的按钮，其可访问名称会带上图标名（"plus 新增事项"），
 *   因此这里不使用首尾锚点，而是用子串匹配并排除图标名干扰。
 */
export function btn(scope: Page | Locator, label: string): Locator {
  const escape = (char: string) => char.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const pattern = label
    .trim()
    .split(/\s+/)
    .map((token) => token.split('').map(escape).join('\\s*'))
    .join('\\s+');
  return scope.getByRole('button', { name: new RegExp(pattern) });
}

export function uniqueName(prefix: string): string {
  return `${prefix}${Date.now().toString(36)}${Math.floor(Math.random() * 1000)}`;
}

/** 时间锚点：对齐到分钟，作为期初资金时点。 */
function anchor(): Date {
  const now = new Date();
  now.setSeconds(0, 0);
  return now;
}

export function dayIso(offset: number, hour = 2, base = anchor()): string {
  return new Date(base.getTime() + offset * 86_400_000 + hour * 3_600_000).toISOString();
}

export interface FixtureUser {
  username: string;
  displayName: string;
  businessName: string;
}

export interface FixtureResult {
  user: FixtureUser;
  eventIds: Record<string, string>;
  base: Date;
}

const CSRF = { 'X-Requested-With': 'XMLHttpRequest' };

export async function registerMerchant(request: APIRequestContext, prefix = 'e2e'): Promise<FixtureUser> {
  const username = uniqueName(prefix);
  const displayName = '端到端掌柜';
  const businessName = '端到端小吃店';
  const response = await request.post('/api/v1/auth/register', {
    headers: CSRF,
    data: {
      username,
      password: PASSWORD,
      display_name: displayName,
      roles: ['merchant'],
      business_name: businessName,
    },
  });
  expect(response.status(), await response.text()).toBe(201);
  return { username, displayName, businessName };
}

/** 建立固定算例：期初 600 元、留底 600 元、三笔未来事项。 */
export async function seedFixture(request: APIRequestContext, user: FixtureUser): Promise<FixtureResult> {
  const base = anchor();

  const snapshot = await request.post('/api/v1/account/snapshots', {
    headers: CSRF,
    data: { opening_balance_cents: 60_000, pending_settlement_cents: 0, snapshot_at: base.toISOString() },
  });
  expect(snapshot.status(), await snapshot.text()).toBe(201);

  const profile = await request.patch('/api/v1/merchant/profile', {
    headers: CSRF,
    data: { default_buffer_amount_cents: 60_000 },
  });
  expect(profile.status()).toBe(200);

  const events = [
    {
      cash_key: 'E2E-SETTLE-0001',
      title: '商户结算款',
      direction: 'inflow',
      amount_cents: 220_000,
      scheduled_at: dayIso(1, 2, base),
      event_type: 'settlement',
      source_label: '结算通知 8821',
      note: '尾号 8821 结算款',
    },
    {
      cash_key: 'E2E-PAY-0001',
      title: '供应商货款',
      direction: 'outflow',
      amount_cents: 100_000,
      scheduled_at: dayIso(2, 2, base),
      event_type: 'supplier_payment',
      source_label: '采购合同 HT-2025-018',
    },
    {
      cash_key: 'E2E-SETTLE-0002',
      title: '平台结算款',
      direction: 'inflow',
      amount_cents: 70_000,
      scheduled_at: dayIso(5, 2, base),
      event_type: 'settlement',
      source_label: '平台账单',
    },
  ];

  const eventIds: Record<string, string> = {};
  for (const payload of events) {
    const created = await request.post('/api/v1/cash-events', { headers: CSRF, data: payload });
    expect(created.status(), await created.text()).toBe(201);
    eventIds[payload.cash_key] = (await created.json()).id;
  }

  return { user, eventIds, base };
}

/** 创建并登录一个商户，返回算例标识。 */
export async function createMerchantFixture(
  request: APIRequestContext,
  prefix = 'e2e',
): Promise<FixtureResult> {
  const user = await registerMerchant(request, prefix);
  return seedFixture(request, user);
}

/** 通过界面登录（验证真实登录表单）。 */
export async function loginViaUi(page: Page, username: string): Promise<void> {
  await page.goto('/login');
  const form = page.locator('.gew-auth__form-inner');
  await form.getByLabel('账户').fill(username);
  await form.getByLabel('密码').fill(PASSWORD);
  await btn(form, '登录').click();
  await expect(page).toHaveURL(/\/(today|consultant|admin|family)/, { timeout: 25_000 });
}

/** 通过界面注册（验证真实注册表单）。 */
export async function registerViaUi(page: Page, username: string, businessName: string): Promise<void> {
  await page.goto('/register');
  const form = page.locator('.gew-auth__form-inner');
  await form.getByLabel('称呼').fill('界面注册掌柜');
  await form.getByLabel('账户名').fill(username);
  await form.getByLabel('经营名称').fill(businessName);
  await form.getByLabel('密码', { exact: true }).fill(PASSWORD);
  await form.getByLabel('确认密码').fill(PASSWORD);
  await btn(form, '注册并登录').click();
  await expect(page).toHaveURL(/\/today/, { timeout: 25_000 });
}

/** 登录后打开某个页面。 */
export async function gotoAuthed(page: Page, path: string): Promise<void> {
  await page.goto(path);
  await expect(page.locator('.gew-header__title')).toBeVisible({ timeout: 20_000 });
}

/**
 * 在浏览器上下文内直接触发点击。
 *
 * 用于窄屏下位于固定容器（底部导航、抽屉头部）内的按钮：视口检查与稳定性等待
 * 会误判，但元素本身可见且可交互。这里先滚动到元素，再派发真实点击事件。
 */
export async function clickInBrowser(scope: Page | Locator, label: string): Promise<void> {
  const target = btn(scope, label);
  // 等待目标出现，避免依赖调用方自己加等待
  await expect(target.first()).toBeAttached({ timeout: 20_000 });
  await target.first().evaluate((node) => {
    (node as HTMLElement).scrollIntoView({ block: 'center', inline: 'center' });
  });
  await target.first().evaluate((node) => {
    (node as HTMLElement).click();
  });
}
