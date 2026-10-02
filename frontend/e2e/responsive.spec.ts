/**
 * 端到端验收（四）：响应式布局与移动端可用性。
 *
 * 这些用例在移动视口（Pixel 5，393×851）下运行，验证：
 * * 底部核心导航取代左侧导航
 * * 窄屏下卡片纵向堆叠、关键信息不丢失
 * * 核心流程（新增事项）在窄屏仍可完成
 *
 * 窄屏下位于固定容器内的按钮使用 clickInBrowser：视口检查会误判，
 * 但元素本身可见且可交互。
 */

import { expect, test } from '@playwright/test';

import { btn, clickInBrowser, createMerchantFixture, loginViaUi } from './helpers';

test.describe('响应式与移动端', () => {
  test('底部核心导航出现且可以切页', async ({ page, request }) => {
    const fixture = await createMerchantFixture(request, 'mobile');
    await loginViaUi(page, fixture.user.username);

    await expect(page.locator('.gew-tabbar')).toBeVisible();
    await expect(page.locator('.gew-tabbar__item')).toHaveCount(5);
    await expect(page.locator('.gew-hero__amount')).toContainText('1,200');

    await clickInBrowser(page.locator('.gew-tabbar'), '现金事件');
    await expect(page).toHaveURL(/\/events/);
    await expect(page.getByRole('heading', { name: '现金事件' })).toBeVisible();
  });

  test('资金卡片在窄屏纵向堆叠且不横向溢出', async ({ page, request }) => {
    const fixture = await createMerchantFixture(request, 'mobilecards');
    await loginViaUi(page, fixture.user.username);

    await expect(page.locator('.gew-hero__label')).toContainText('今日可提用');

    const boxes = await page.locator('.gew-card').evaluateAll((nodes) =>
      nodes.slice(0, 4).map((node) => {
        const rect = node.getBoundingClientRect();
        return { x: Math.round(rect.x), y: Math.round(rect.y), width: Math.round(rect.width) };
      }),
    );
    expect(boxes.length).toBeGreaterThanOrEqual(2);
    // 窄屏：一行最多两张卡片，第三张开始换行
    const distinctRows = new Set(boxes.map((item) => item.y));
    expect(distinctRows.size).toBeGreaterThanOrEqual(2);

    // 页面不应出现横向滚动
    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
    );
    expect(overflow).toBeLessThanOrEqual(1);
  });

  test('表单在窄屏可以完成提交流程', async ({ page, request }) => {
    const fixture = await createMerchantFixture(request, 'mobilenew');
    await loginViaUi(page, fixture.user.username);

    await clickInBrowser(page.locator('.gew-tabbar'), '现金事件');
    await expect(page.getByRole('heading', { name: '现金事件' })).toBeVisible({ timeout: 25_000 });

    await clickInBrowser(page, '新增事项');
    await page.getByLabel('事项名称').fill('移动端录入');
    await page.getByLabel('金额（元）').fill('120');

    // 抽屉在窄屏占满宽度
    const drawerWidth = await page
      .locator('.ant-drawer-content-wrapper')
      .evaluate((node) => Math.round(node.getBoundingClientRect().width));
    const viewportWidth = page.viewportSize()?.width ?? 0;
    expect(drawerWidth).toBeLessThanOrEqual(viewportWidth + 1);

    await clickInBrowser(page, '创建事项');
    await expect(page.getByText('确认新增事项')).toBeVisible();
    await clickInBrowser(page.locator('.ant-modal-root'), '确认创建');
    await expect(page.getByText('事项已创建')).toBeVisible({ timeout: 15_000 });
  });

  test('首页主要信息在窄屏仍然完整', async ({ page, request }) => {
    const fixture = await createMerchantFixture(request, 'mobileinfo');
    await loginViaUi(page, fixture.user.username);

    await expect(page.locator('.gew-hero__amount')).toBeVisible({ timeout: 25_000 });
    await expect(page.getByText('当前可用')).toBeVisible();
    await expect(page.getByText('待结算资金')).toBeVisible();
    await expect(page.getByText('最紧张资金时点')).toBeVisible();
  });

  test('窄屏输入框有足够的点击高度', async ({ page, request }) => {
    const fixture = await createMerchantFixture(request, 'mobiletouch');
    await loginViaUi(page, fixture.user.username);
    await clickInBrowser(page.locator('.gew-tabbar'), '现金事件');
    await expect(page.getByRole('heading', { name: '现金事件' })).toBeVisible({ timeout: 25_000 });

    await clickInBrowser(page, '新增事项');
    const box = await page.getByLabel('事项名称').boundingBox();
    expect(box?.height ?? 0).toBeGreaterThanOrEqual(32);
  });

  test('未登录访问在窄屏同样跳转登录', async ({ page }) => {
    await page.goto('/family');
    await expect(page).toHaveURL(/\/login/, { timeout: 20_000 });
    await expect(page.locator('.gew-auth__form-inner')).toBeVisible();
  });

  test('窄屏下表格容器内部滚动而不撑破页面', async ({ page, request }) => {
    const fixture = await createMerchantFixture(request, 'mobiletable');
    await loginViaUi(page, fixture.user.username);
    await clickInBrowser(page.locator('.gew-tabbar'), '现金事件');
    await expect(page.getByRole('heading', { name: '现金事件' })).toBeVisible({ timeout: 25_000 });
    await expect(page.getByRole('cell', { name: /商户结算款/ })).toBeVisible({ timeout: 25_000 });

    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
    );
    expect(overflow).toBeLessThanOrEqual(1);
    expect(await btn(page, '新增事项').count()).toBeGreaterThan(0);
  });
});
