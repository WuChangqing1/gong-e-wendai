/**
 * 端到端验收（一）：认证与今日决策。
 *
 * 针对「同源单端口」生产形态运行（FastAPI 同时提供 /api/* 与前端 build）。
 * 按钮定位统一使用 helpers 中的 btn()，以兼容 Ant Design 在汉字之间插入空格。
 */

import { expect, test } from '@playwright/test';

import {
  PASSWORD,
  appUrl,
  btn,
  createMerchantFixture,
  gotoAuthed,
  loginViaUi,
  registerViaUi,
  uniqueName,
} from './helpers';

test.describe('认证', () => {
  test('注册 → 自动进入今日决策', async ({ page }) => {
    const username = uniqueName('ui');
    await registerViaUi(page, username, '界面注册小吃店');
    await expect(page.getByText('今日决策').first()).toBeVisible();
    await expect(page.getByText('先登记当前经营资金')).toBeVisible();
  });

  test('退出后可登录，错误密码给出明确提示', async ({ page }) => {
    const username = uniqueName('login');
    await registerViaUi(page, username, '登录测试店');

    await page.goto(appUrl('/settings'));
    await btn(page, '退出登录').first().click();
    await expect(page).toHaveURL(/\/login/, { timeout: 20_000 });

    const form = page.locator('.gew-auth__form-inner');
    await form.getByLabel('账户').fill(username);
    await form.getByLabel('密码').fill('WrongPass123');
    await btn(form, '登录').click();
    await expect(page.getByText('用户名或密码不正确')).toBeVisible();

    await form.getByLabel('密码').fill(PASSWORD);
    await btn(form, '登录').click();
    await expect(page).toHaveURL(/\/today/, { timeout: 25_000 });
  });

  test('未登录访问业务页面会跳转到登录', async ({ page }) => {
    await page.goto(appUrl('/events'));
    await expect(page).toHaveURL(/\/login/, { timeout: 20_000 });
  });

  test('刷新页面保持登录状态', async ({ page, request }) => {
    const fixture = await createMerchantFixture(request, 'refresh');
    await loginViaUi(page, fixture.user.username);
    await page.goto(appUrl('/today'));
    await page.reload();
    await expect(page.getByText('今日决策').first()).toBeVisible({ timeout: 25_000 });
  });
});

test.describe('今日决策', () => {
  test('可提用金额为 1200 元并说明限制原因', async ({ page, request }) => {
    const fixture = await createMerchantFixture(request, 'today');
    await loginViaUi(page, fixture.user.username);
    await gotoAuthed(page, '/today');

    await expect(page.locator('.gew-hero__label')).toContainText('今日可提用');
    const hero = page.locator('.gew-hero__amount');
    await expect(hero).toContainText('1,200');
    await expect(hero).toContainText('00');

    await expect(page.getByText('当前可用')).toBeVisible();
    await expect(page.getByText('待结算资金')).toBeVisible();
    await expect(page.getByText('未来 7 天收入')).toBeVisible();
    await expect(page.getByText('未来 7 天支出')).toBeVisible();
    await expect(page.getByText('当前不可作为可用经营资金')).toBeVisible();

    await expect(page.getByText('最紧张资金时点')).toBeVisible();
    await expect(page.getByText('供应商货款').first()).toBeVisible();

    await expect(page.getByText('缺口情况')).toBeVisible();
    await expect(page.getByText('付款缺口').first()).toBeVisible();
    await expect(page.getByText('留底缺口').first()).toBeVisible();
    await expect(page.getByText(/不能相加/)).toBeVisible();
  });

  test('查看原因抽屉展示完整余额推演', async ({ page, request }) => {
    const fixture = await createMerchantFixture(request, 'reason');
    await loginViaUi(page, fixture.user.username);
    await gotoAuthed(page, '/today');

    await btn(page, '查看计算依据').click();
    await expect(page.locator('.ant-drawer-title')).toContainText('计算依据');
    await expect(page.getByText('基础数据')).toBeVisible();
    await expect(page.getByText('余额推演')).toBeVisible();
    await expect(page.getByText('结论', { exact: true })).toBeVisible();
    await expect(page.getByText('计算引擎版本')).toBeVisible();
    await expect(page.getByText('最紧时点尚未到账的收入')).toBeVisible();
  });

  test('未来趋势图渲染且图例用文字表达风险', async ({ page, request }) => {
    const fixture = await createMerchantFixture(request, 'chart');
    await loginViaUi(page, fixture.user.username);
    await gotoAuthed(page, '/today');

    await expect(page.locator('.gew-chart canvas')).toBeVisible({ timeout: 25_000 });
    await expect(page.getByText('0 元线（付款缺口）')).toBeVisible();
    await expect(page.getByText(/经营留底 ¥600\.00/)).toBeVisible();
    await expect(page.getByText(/可提用 ¥1,200\.00/)).toBeVisible();
  });

  test('到账延迟情景可提用归零', async ({ page, request }) => {
    const fixture = await createMerchantFixture(request, 'delayed');
    await loginViaUi(page, fixture.user.username);
    await gotoAuthed(page, '/today');

    await page.locator('.ant-segmented-item').filter({ hasText: '到账延迟' }).click();
    await expect(page.locator('.gew-hero__amount')).toContainText('0', { timeout: 25_000 });
    await expect(page.getByText('当前不建议从经营资金中提用家庭资金')).toBeVisible();
  });

  test('共同约束取最保守上限并标注来源情景', async ({ page, request }) => {
    const fixture = await createMerchantFixture(request, 'joint');
    await loginViaUi(page, fixture.user.username);
    await gotoAuthed(page, '/today');

    await page.locator('.ant-segmented-item').filter({ hasText: '共同约束' }).click();
    await expect(page.locator('.gew-hero__amount')).toContainText('0', { timeout: 25_000 });
    await expect(page.getByText(/最保守的结果来自「到账延迟」情景/)).toBeVisible();
  });
});
