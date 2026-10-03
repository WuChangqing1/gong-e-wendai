/**
 * 端到端验收（一）：认证与今日决策。
 *
 * 针对「同源单端口」生产形态运行（FastAPI 同时提供 /api/* 与前端 build）。
 * 按钮定位统一使用 helpers 中的 btn()，以兼容 Ant Design 在汉字之间插入空格。
 */

import { expect, test } from '@playwright/test';

import {
  PASSWORD,
  apiUrl,
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

  test('旧的管理后台地址不再渲染后台（V3 已移除 Admin）', async ({ page, request }) => {
    const fixture = await createMerchantFixture(request, 'removedadmin');
    await loginViaUi(page, fixture.user.username);

    // 前端不再有 /admin 路由：命中兜底路由，渲染「页面不存在」。
    await page.goto(appUrl('/admin'));
    await expect(page.getByText('页面不存在')).toBeVisible({ timeout: 20_000 });
    await expect(page.getByText('系统概览')).toBeHidden();
    await expect(page.getByText('用户管理')).toBeHidden();

    // 旧接口也必须表现为「不存在」，而不是「无权限」。
    const apiProbe = await request.get(apiUrl('admin', 'overview'));
    expect(apiProbe.status()).toBe(404);
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

    await expect(page.locator('.gew-hero__label')).toContainText('今日最多可提用');
    const hero = page.locator('.gew-hero__amount');
    await expect(hero).toContainText('1,200');
    await expect(hero).toContainText('00');

    await expect(page.getByText('当前可用')).toBeVisible();
    // 待结算资金卡片：文案可能同时出现在提示气泡与说明段落里，这里限定在资金卡片内
    await expect(
      page.locator('.gew-metric').filter({ hasText: '待结算资金' }).first(),
    ).toBeVisible();
    await expect(page.getByText('未来 7 天收入')).toBeVisible();
    await expect(page.getByText('未来 7 天支出')).toBeVisible();
    await expect(page.getByText('当前不可作为可用经营资金')).toBeVisible();

    await expect(page.getByText('最紧张资金时点')).toBeVisible();
    await expect(page.getByText('已确认退款').first()).toBeVisible();

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
    await expect(page.getByText('2.0.0')).toBeVisible();
  });

  test('未来趋势图渲染且图例用文字表达风险', async ({ page, request }) => {
    const fixture = await createMerchantFixture(request, 'chart');
    await loginViaUi(page, fixture.user.username);
    await gotoAuthed(page, '/today');

    // 图表区容器先出现，ECharts 初始化后再出现 canvas
    await expect(page.locator('.gew-chart').first()).toBeVisible({ timeout: 25_000 });
    await page.waitForFunction(
      () => document.querySelectorAll('.gew-chart canvas').length > 0,
      undefined,
      { timeout: 25_000 },
    );
    await expect(page.getByText('0 元线（付款缺口）')).toBeVisible();
    await expect(page.getByText(/经营留底 ¥600\.00/)).toBeVisible();
    // 「可提用 ¥1,200.00」同时出现在图例与资金安排参考里，限定到趋势图卡片内
    await expect(
      page
        .locator('.gew-card')
        .filter({ hasText: '未来 7 天资金趋势' })
        .getByText(/可提用 ¥1,200\.00/),
    ).toBeVisible();
  });

  test('到账延迟情景不能提用，且不得描述为资金安排可行', async ({ page, request }) => {
    const fixture = await createMerchantFixture(request, 'delayed');
    await loginViaUi(page, fixture.user.username);
    await gotoAuthed(page, '/today');

    await page.locator('.ant-segmented-item').filter({ hasText: '到账延迟' }).click();
    // 首屏改为展示缺口金额，而不是把 0 当成最醒目的唯一信息
    await expect(page.locator('.gew-hero__label')).toContainText('当前存在付款缺口', {
      timeout: 25_000,
    });
    await expect(page.locator('.gew-hero__amount')).toContainText('200');
    await expect(page.getByText('暂不建议提用家庭资金')).toBeVisible();
    // 首屏结论区不得出现「资金安排可行」这类结论标签
    await expect(page.locator('.gew-hero__label')).not.toContainText('资金安排可行');
  });

  test('共同约束取最保守上限并说明 0 不代表可行', async ({ page, request }) => {
    const fixture = await createMerchantFixture(request, 'joint');
    await loginViaUi(page, fixture.user.username);
    await gotoAuthed(page, '/today');

    await page.locator('.ant-segmented-item').filter({ hasText: '共同约束' }).click();
    await expect(page.locator('.gew-hero__label')).toContainText('当前存在付款缺口', {
      timeout: 25_000,
    });
    await expect(page.getByText(/最保守的结果来自「到账延迟」情景/)).toBeVisible();
    await expect(page.locator('.gew-hero__label')).not.toContainText('资金安排可行');
    // 首屏必须明确说明「即使不提用也仍存在缺口」
    await expect(page.locator('.gew-hero')).toContainText('即使不提用');
  });
});
