/**
 * 端到端验收（五）：图表化分析。
 *
 * 验证今日决策页与情景分析页上的图表真实渲染，且数据与计算结果一致。
 */

import { expect, test } from '@playwright/test';

import { apiUrl, appUrl, btn, createMerchantFixture, gotoAuthed, loginViaUi } from './helpers';

test.describe('图表化分析', () => {
  test('今日决策页展示每日收支与到账分布图', async ({ page, request }) => {
    const fixture = await createMerchantFixture(request, 'charts');
    await loginViaUi(page, fixture.user.username);
    await gotoAuthed(page, '/today');

    // 阶梯线趋势图
    await expect(page.getByText('未来 7 天资金趋势')).toBeVisible({ timeout: 25_000 });

    // 本期资金概览的关键指标
    await expect(page.getByText('本期资金概览')).toBeVisible();
    await expect(page.getByText('最低余额日')).toBeVisible();
    await expect(page.getByText('支出最多的一天')).toBeVisible();

    // 每日收支柱状图与到账分布图
    await expect(page.getByText('每日收入与支出')).toBeVisible();
    await expect(page.getByText('待结算资金到账分布')).toBeVisible();

    // 图表真实渲染为 canvas
    const canvases = page.locator('.gew-chart canvas');
    await expect(canvases.first()).toBeVisible({ timeout: 25_000 });
    expect(await canvases.count()).toBeGreaterThanOrEqual(3);
  });

  test('情景分析页展示全部图表', async ({ page, request }) => {
    const fixture = await createMerchantFixture(request, 'chartfull');
    await loginViaUi(page, fixture.user.username);
    await gotoAuthed(page, '/analysis');

    await expect(page.getByText('资金曲线对比')).toBeVisible({ timeout: 25_000 });
    await expect(page.getByText('本期资金概览')).toBeVisible();
    await expect(page.getByText('每日收入与支出')).toBeVisible();
    await expect(page.getByText('收支结构')).toBeVisible();
    await expect(page.getByText('余额变化过程')).toBeVisible();
    await expect(page.getByText('资金积累节奏')).toBeVisible();
    await expect(page.getByText('待结算资金到账分布')).toBeVisible();
    await expect(page.getByText('情景关键指标对比')).toBeVisible();

    const canvases = page.locator('.gew-chart canvas');
    await expect(canvases.first()).toBeVisible({ timeout: 25_000 });
    // 阶梯线 + 6 个新增图表
    expect(await canvases.count()).toBeGreaterThanOrEqual(6);
  });

  test('饼图可在收入与支出结构之间切换', async ({ page, request }) => {
    const fixture = await createMerchantFixture(request, 'pie');
    await loginViaUi(page, fixture.user.username);
    await gotoAuthed(page, '/analysis');

    await expect(page.getByText('收支结构')).toBeVisible({ timeout: 25_000 });
    await expect(page.getByText('结算款').first()).toBeVisible();

    await page.locator('.ant-segmented-item').filter({ hasText: '支出结构' }).click();
    await expect(page.getByText('进货款').first()).toBeVisible({ timeout: 15_000 });
  });

  test('计算详情收进可展开区域', async ({ page, request }) => {
    const fixture = await createMerchantFixture(request, 'collapsed');
    await loginViaUi(page, fixture.user.username);
    await gotoAuthed(page, '/analysis');

    const toggle = page.getByText('计算详情');
    await expect(toggle).toBeVisible({ timeout: 25_000 });

    // 默认收起：共同约束口径说明不可见
    const note = page.getByText(/同时满足所有情景的可提用上限/);
    await expect(note).toBeHidden();

    await toggle.click();
    await expect(note).toBeVisible({ timeout: 15_000 });
  });

  test('用户界面不出现开发口径说明', async ({ page, request }) => {
    const fixture = await createMerchantFixture(request, 'nofdevcopy');
    await loginViaUi(page, fixture.user.username);

    for (const [route, forbidden] of [
      ['/today', ['确定性计算引擎', '计算引擎版本', '字段：']],
      ['/analysis', ['说明与口径', '换成图形表达']],
      ['/settings', ['API Key', '密钥', '环境变量', '文字识别模型']],
    ] as const) {
      await gotoAuthed(page, route);
      await page.waitForTimeout(1200);
      const text = await page.locator('body').innerText();
      for (const word of forbidden) {
        expect(text, `${route} 不应出现「${word}」`).not.toContain(word);
      }
    }
  });

  test('历史不足时的提示不重复', async ({ page, request }) => {
    // 无历史数据时后端提示里已经带了「其它功能照常可用」，
    // 前端不能再补一句同样的说明，否则同一句话会连着出现两次。
    const fixture = await createMerchantFixture(request, 'nodup');
    await loginViaUi(page, fixture.user.username);
    await gotoAuthed(page, '/today');
    await expect(page.getByText('未来 7 天收付趋势').first()).toBeVisible({ timeout: 25_000 });
    await page.waitForTimeout(800);

    const text = await page.locator('body').innerText();
    const occurrences = text.split('照常可用').length - 1;
    expect(occurrences, '「照常可用」不应重复出现').toBeLessThanOrEqual(1);
  });

  test('窗口聚合接口与引擎口径一致', async ({ page, request }) => {
    const fixture = await createMerchantFixture(request, 'apisum');
    await loginViaUi(page, fixture.user.username);

    const summary = await page.request.get(apiUrl('analysis', 'window-summary'));
    expect(summary.status()).toBe(200);
    const body = await summary.json();
    const analysis = await (await page.request.get(apiUrl('analysis', 'today'))).json();

    expect(body.window_days).toBe(7);
    expect(body.opening_balance_cents).toBe(analysis.opening_balance_cents);
    expect(body.scheduled_inflow_cents).toBe(analysis.window_inflow_cents);
    expect(body.scheduled_outflow_cents).toBe(analysis.window_outflow_cents);
    expect(body.closing_balance_cents).toBe(
      body.opening_balance_cents + body.net_change_cents,
    );
    expect(body.daily_terms.length).toBe(8);
    expect(body.category_terms.length).toBeGreaterThan(0);
    expect(body.arrival_terms.length).toBeGreaterThan(0);
  });

  test('无事项时图表给出明确空状态', async ({ page, request }) => {
    const username = `empty${Date.now().toString(36)}`;
    const registered = await request.post(apiUrl('auth', 'register'), {
      headers: { 'X-Requested-With': 'XMLHttpRequest' },
      data: {
        username,
        password: 'Wendai2025',
        display_name: '空数据掌柜',
        roles: ['merchant'],
        business_name: '空数据小店',
      },
    });
    expect(registered.status()).toBe(201);

    await loginViaUi(page, username);
    await gotoAuthed(page, '/today');
    await expect(page.getByText('未来 7 天还没有已确认的收付款事项').first()).toBeVisible({
      timeout: 25_000,
    });
  });

  test('移动端图表可正常渲染且不横向溢出', async ({ page, request }, testInfo) => {
    test.skip(testInfo.project.name !== 'mobile', '仅在移动端项目执行');
    const fixture = await createMerchantFixture(request, 'chartmobile');
    await loginViaUi(page, fixture.user.username);

    await expect(page.locator('.gew-chart canvas').first()).toBeVisible({ timeout: 25_000 });
    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
    );
    expect(overflow).toBeLessThanOrEqual(1);
    // 窄屏隐藏左侧导航，图表卡片占满可用宽度
    await expect(page.locator('.gew-sider')).toBeHidden();
    expect(await btn(page, '查看计算依据').count()).toBeGreaterThan(0);
    expect(appUrl('/today')).toContain('/today');
  });
});
