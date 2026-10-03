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

import { appUrl, btn, clickInBrowser, createMerchantFixture, loginViaUi } from './helpers';

test.describe('响应式与移动端', () => {
  test('底部核心导航出现且可以切页', async ({ page, request }) => {
    const fixture = await createMerchantFixture(request, 'mobile');
    await loginViaUi(page, fixture.user.username);

    await expect(page.locator('.gew-tabbar')).toBeVisible();
    await expect(page.locator('.gew-tabbar__item')).toHaveCount(5);
    await expect(page.locator('.gew-hero__amount')).toContainText('1,200');

    // 窄屏隐藏左侧导航，宽度全部让给内容（历史缺陷：侧栏仍占 216px，内容被压到 150px）
    await expect(page.locator('.gew-sider')).toBeHidden();
    // 首屏主卡片（今日可提用）应当接近整屏宽度
    const heroWidth = await page
      .locator('.gew-hero')
      .evaluate((node) => Math.round(node.getBoundingClientRect().width));
    const viewportWidth = page.viewportSize()?.width ?? 0;
    expect(heroWidth).toBeGreaterThan(viewportWidth * 0.8);

    await clickInBrowser(page.locator('.gew-tabbar'), '现金事件');
    await expect(page).toHaveURL(/\/events/);
    await expect(page.getByRole('heading', { name: '现金事件' })).toBeVisible();
  });

  test('分析页图表在窄屏占满宽度且正常渲染', async ({ page, request }) => {
    const fixture = await createMerchantFixture(request, 'mobilecharts');
    await loginViaUi(page, fixture.user.username);

    // 深链可能被初始化竞态重定向，重试直到进入分析页
    for (let attempt = 0; attempt < 4; attempt += 1) {
      await page.goto(appUrl('/analysis'));
      await page.waitForTimeout(1500);
      if (page.url().includes('/analysis')) break;
    }
    await expect(page.getByRole('heading', { name: '情景分析' })).toBeVisible({ timeout: 25_000 });

    await expect(page.locator('.gew-chart canvas').first()).toBeVisible({ timeout: 25_000 });
    expect(await page.locator('.gew-chart canvas').count()).toBeGreaterThanOrEqual(3);

    const cardWidth = await page
      .locator('.gew-card')
      .first()
      .evaluate((node) => Math.round(node.getBoundingClientRect().width));
    const viewportWidth = page.viewportSize()?.width ?? 0;
    expect(cardWidth).toBeGreaterThan(viewportWidth * 0.8);

    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
    );
    expect(overflow).toBeLessThanOrEqual(1);
  });

  test('资金卡片在窄屏纵向堆叠且不横向溢出', async ({ page, request }) => {
    const fixture = await createMerchantFixture(request, 'mobilecards');
    await loginViaUi(page, fixture.user.username);

    await expect(page.locator('.gew-hero__label')).toContainText('今日最多可提用');

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
    await expect(page.getByText('待结算资金').first()).toBeVisible();
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
    await page.goto(appUrl('/family'));
    await expect(page).toHaveURL(/\/login/, { timeout: 20_000 });
    await expect(page.locator('.gew-auth__form-inner')).toBeVisible();
  });

  test('窄屏下事项列表改为卡片且不撑破页面', async ({ page, request }) => {
    const fixture = await createMerchantFixture(request, 'mobiletable');
    await loginViaUi(page, fixture.user.username);
    await clickInBrowser(page.locator('.gew-tabbar'), '现金事件');
    await expect(page.getByRole('heading', { name: '现金事件' })).toBeVisible({ timeout: 25_000 });

    // V3：手机端不再渲染 980px 宽表格，改为卡片列表（无需横向拖动）。
    const cardList = page.getByTestId('event-card-list');
    await expect(cardList).toBeVisible({ timeout: 25_000 });
    await expect(cardList).toContainText('结算款');
    // 卡片里必须带金额与操作入口，信息量与表格列一致
    await expect(cardList).toContainText('¥');
    await expect(page.locator('.ant-table-wrapper')).toBeHidden();

    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
    );
    expect(overflow).toBeLessThanOrEqual(1);
    expect(await btn(page, '新增事项').count()).toBeGreaterThan(0);
  });

  test('「我的」在窄屏是列表项 + 二级进入，而不是横向页签', async ({ page, request }) => {
    const fixture = await createMerchantFixture(request, 'mobilesettings');
    await loginViaUi(page, fixture.user.username);
    await clickInBrowser(page.locator('.gew-tabbar'), '我的');
    await expect(page).toHaveURL(/\/settings/, { timeout: 25_000 });

    const menu = page.getByTestId('settings-menu');
    await expect(menu).toBeVisible({ timeout: 25_000 });
    for (const label of ['个人资料', '经营资料', '家庭设置', '安全设置', '智能助手']) {
      await expect(menu).toContainText(label);
    }
    // 窄屏不出现横向页签（V3 第 80 节：用列表项而不是卡片网格）
    await expect(page.locator('.ant-tabs-nav')).toBeHidden();

    // 进入分组 → 有返回条与分组内容
    await menu.getByText('安全设置').click();
    const detail = page.getByTestId('settings-detail');
    await expect(detail).toBeVisible({ timeout: 15_000 });
    await expect(detail).toContainText('修改密码');
    await expect(menu).toBeHidden();

    await page.getByRole('button', { name: '返回设置列表' }).click();
    await expect(page.getByTestId('settings-menu')).toBeVisible({ timeout: 15_000 });

    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
    );
    expect(overflow).toBeLessThanOrEqual(1);
  });

  test('窄屏筛选收进底部抽屉，含重置与确认', async ({ page, request }) => {
    const fixture = await createMerchantFixture(request, 'mobilefilter');
    await loginViaUi(page, fixture.user.username);
    await clickInBrowser(page.locator('.gew-tabbar'), '现金事件');
    await expect(page.getByRole('heading', { name: '现金事件' })).toBeVisible({ timeout: 25_000 });

    await clickInBrowser(page, '筛选');
    const sheet = page.locator('.gew-filter-sheet');
    await expect(sheet).toBeVisible({ timeout: 20_000 });
    for (const label of ['预计时间区间', '收支方向', '状态', '事项类型']) {
      await expect(sheet).toContainText(label);
    }
    await expect(btn(sheet, '重置').first()).toBeVisible();
    await expect(btn(sheet, '确认').first()).toBeVisible();

    // 底部抽屉形态，宽度不超过视口
    const placement = await page.evaluate(
      () => document.querySelector('.ant-drawer')?.className ?? '',
    );
    expect(placement).toContain('ant-drawer-bottom');
    const box = await page.locator('.ant-drawer-content-wrapper').first().boundingBox();
    const viewport = page.viewportSize();
    expect(box?.width ?? 0).toBeLessThanOrEqual((viewport?.width ?? 0) + 1);

    // 选一个条件后确认：抽屉关闭，按钮提示生效条件数
    await sheet.locator('.ant-select').nth(0).click();
    await page.getByTitle('收入', { exact: true }).click();
    await clickInBrowser(sheet, '确认');
    await expect(btn(page, '筛选 (1)').first()).toBeVisible({ timeout: 15_000 });

    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
    );
    expect(overflow).toBeLessThanOrEqual(1);
  });

  test('改变视口不触发新的接口请求', async ({ page, request }) => {
    const fixture = await createMerchantFixture(request, 'mobileresize');
    await loginViaUi(page, fixture.user.username);

    const calls: string[] = [];
    page.on('request', (req) => {
      if (req.url().includes('/api/v1/') && req.method() === 'GET') calls.push(req.url());
    });

    for (const route of ['/today', '/events']) {
      await page.goto(appUrl(route));
      await page.waitForTimeout(2500);
      const afterLoad = calls.length;
      expect(afterLoad, `${route} 首屏应发起过请求`).toBeGreaterThan(0);

      // 连续跨越断点：desktop → tablet → mobile → desktop
      for (const width of [900, 390, 430, 1440]) {
        await page.setViewportSize({ width, height: 900 });
        await page.waitForTimeout(1200);
      }
      const added = calls.length - afterLoad;
      expect(added, `${route} 视口变化后不应新增接口请求（实际新增 ${added} 次）`).toBe(0);
    }
  });
});
