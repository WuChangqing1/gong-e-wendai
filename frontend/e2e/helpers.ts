/**
 * 端到端测试共用装置。
 *
 * 直接测试「同源单端口」运行形态：FastAPI 同时提供 /api/v1/* 与前端 build。
 * 通过 API 请求上下文（真实 HTTP + Cookie）创建固定算例数据，
 * 再用真实浏览器验证界面，保证测试跑的就是生产形态。
 *
 * 关于基路径：应用既可能部署在站点根路径，也可能挂在父站点的子路径下
 * （例如 https://example.com/wendai/）。所有页面跳转与接口地址都通过
 * appUrl() / apiUrl() 拼接，因此同一套用例可以直接跑子路径部署：
 *
 *   E2E_BASE_URL=http://127.0.0.1:8000                 # 根路径
 *   E2E_BASE_URL=https://ccqspace.site/wendai          # 子路径
 */

import { expect, type APIRequestContext, type Locator, type Page } from '@playwright/test';

export const PASSWORD = 'Wendai2025';

/** 应用基路径，例如 '' 或 '/wendai'。 */
export function appBase(): string {
  const raw = process.env.E2E_BASE_URL || 'http://127.0.0.1:8000';
  const withoutTrailing = raw.replace(/\/+$/, '');
  const match = withoutTrailing.match(/^(https?:\/\/[^/]+)(\/.*)?$/);
  if (!match) return '';
  return (match[2] || '').replace(/\/+$/, '');
}

/** 把应用内路径拼接为可访问的 URL。 */
export function appUrl(path: string): string {
  const base = appBase();
  if (!path || path === '/') return base ? `${base}/` : '/';
  const suffix = path.startsWith('/') ? path : `/${path}`;
  return `${base}${suffix}` || suffix;
}

/** 把接口路径拼接为带基路径的 API 地址。 */
export function apiUrl(...segments: string[]): string {
  const parts = segments.map((item) => item.replace(/^\/+|\/+$/g, '')).filter(Boolean);
  return `${appBase()}/api/v1/${parts.join('/')}`;
}

/**
 * 按文案定位按钮，兼容 Ant Design 的渲染细节。
 *
 * * 相邻汉字之间会插入空格（"登录" 渲染为 "登 录"）
 * * 中文与拉丁字母之间会留出空格（"导入 CSV" 渲染为 "导 入 CSV"）
 * * 带图标的按钮，其可访问名称会带上图标名（"plus 新增事项"），
 *   因此这里不使用首尾锚点，而是用子串匹配排除图标名干扰。
 */
/**
 * 按文案定位按钮，兼容 Ant Design 的渲染细节。
 *
 * * 相邻汉字之间会插入空格（"登录" 渲染为 "登 录"）
 * * 中文与拉丁字母之间会留出空格（"导入 CSV" 渲染为 "导 入 CSV"）
 * * 带图标的按钮，其可访问名称会带上图标名（"plus 新增事项"），
 *   因此不使用首尾锚点，而是用子串匹配
 * * 同一文案可能同时出现在页面头部与空状态里（例如「新增事项」），
 *   也存在抽屉头部这类不在 `main` 内的按钮，因此统一取第一个匹配项，
 *   避免元素解析歧义
 */
export function btn(scope: Page | Locator, label: string): Locator {
  const escape = (char: string) => char.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const pattern = label
    .trim()
    .split(/\s+/)
    .map((token) => token.split('').map(escape).join('\\s*'))
    .join('\\s+');
  return scope.getByRole('button', { name: new RegExp(pattern) }).first();
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

export async function registerMerchant(
  request: APIRequestContext,
  prefix = 'e2e',
): Promise<FixtureUser> {
  const username = uniqueName(prefix);
  const displayName = '端到端掌柜';
  const businessName = '端到端小吃店';
  const response = await request.post(apiUrl('auth', 'register'), {
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
export async function seedFixture(
  request: APIRequestContext,
  user: FixtureUser,
): Promise<FixtureResult> {
  const base = anchor();

  const snapshot = await request.post(apiUrl('account', 'snapshots'), {
    headers: CSRF,
    data: {
      opening_balance_cents: 60_000,
      pending_settlement_cents: 0,
      snapshot_at: base.toISOString(),
    },
  });
  expect(snapshot.status(), await snapshot.text()).toBe(201);

  const profile = await request.patch(apiUrl('merchant', 'profile'), {
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
    const created = await request.post(apiUrl('cash-events'), {
      headers: CSRF,
      data: payload,
    });
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
  await page.goto(appUrl('/login'));
  const form = page.locator('.gew-auth__form-inner');
  await form.getByLabel('账户').fill(username);
  await form.getByLabel('密码').fill(PASSWORD);
  await btn(form, '登录').click();
  await expect(page).toHaveURL(/\/(today|consultant|admin|family)/, { timeout: 25_000 });
}

/** 通过界面注册（验证真实注册表单）。 */
export async function registerViaUi(
  page: Page,
  username: string,
  businessName: string,
): Promise<void> {
  await page.goto(appUrl('/register'));
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
  await page.goto(appUrl(path));
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
  await expect(target.first()).toBeAttached({ timeout: 20_000 });
  await target.first().evaluate((node) => {
    (node as HTMLElement).scrollIntoView({ block: 'center', inline: 'center' });
  });
  await target.first().evaluate((node) => {
    (node as HTMLElement).click();
  });
}
