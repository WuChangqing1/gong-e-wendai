/**
 * 今日决策页的「同页口径一致」验收。
 *
 * 需求：切换 按当前计划 / 到账延迟 / 共同约束 之后，
 * 顶部 Hero、本期资金概览（图表聚合）、资金规划里的当前结果必须同步变化，
 * 不允许出现「顶部缺口、下方资金可行」这种自相矛盾。
 */

import { expect, test, type Page } from '@playwright/test';

import { createMerchantFixture, gotoAuthed, loginViaUi } from './helpers';

const MODE_CASES = [
  { mode: '按当前计划', expected: '资金安排可行', apiMode: 'current_plan', status: 'FEASIBLE' },
  { mode: '到账延迟', expected: '存在付款缺口', apiMode: 'delayed', status: 'PAYMENT_GAP' },
  { mode: '共同约束', expected: '存在付款缺口', apiMode: 'joint', status: 'PAYMENT_GAP' },
] as const;

function hero(page: Page) {
  return page.locator('.gew-hero');
}

function planCard(page: Page) {
  return page.locator('.gew-card').filter({ hasText: '资金规划' }).first();
}

test.describe('今日决策同页口径', () => {
  test('切换口径后顶部与资金规划同步，窗口聚合也跟着换口径', async ({ page, request }) => {
    const fixture = await createMerchantFixture(request, 'modesync');
    await loginViaUi(page, fixture.user.username);
    await gotoAuthed(page, '/today');

    const summaries: { mode?: string; delay_days?: number; status?: string }[] = [];
    const requested: string[] = [];
    page.on('request', (item) => {
      if (item.url().includes('window-summary')) requested.push(item.url());
    });
    page.on('response', async (item) => {
      if (!item.url().includes('window-summary')) return;
      try {
        summaries.push(await item.json());
      } catch {
        // 忽略被取消的请求
      }
    });

    for (const item of MODE_CASES) {
      await page.locator('.ant-segmented-item', { hasText: item.mode }).first().click();

      // 顶部 Hero：状态与金额都来自当前口径
      await expect(hero(page)).toContainText(item.expected, { timeout: 25_000 });
      // 资金规划：当前结论必须是同一份结果，且写明是哪个口径
      await expect(planCard(page)).toContainText(`${item.mode}：`, { timeout: 25_000 });
      await expect(planCard(page)).toContainText(`（状态：${item.expected}）`);

      // 图表聚合接口必须带上当前口径（后端按该口径聚合，缓存 key 也区分）
      await expect
        .poll(() => requested.some((url) => url.includes(`mode=${item.apiMode}`)), {
          timeout: 25_000,
        })
        .toBe(true);
      await expect
        .poll(() => summaries.filter((entry) => entry.mode === item.apiMode).length, {
          timeout: 25_000,
        })
        .toBeGreaterThan(0);
    }

    // 共同约束：必须显示真正绑定的那个情景
    await expect(planCard(page)).toContainText('最保守情况：');
  });

  test('不可行口径下，顶部与资金规划都不出现「资金安排可行」', async ({ page, request }) => {
    const fixture = await createMerchantFixture(request, 'modenotok');
    await loginViaUi(page, fixture.user.username);
    await gotoAuthed(page, '/today');

    await page.locator('.ant-segmented-item', { hasText: '到账延迟' }).first().click();
    await expect(hero(page)).toContainText('存在付款缺口', { timeout: 25_000 });
    await expect(planCard(page)).toContainText('（状态：存在付款缺口）', { timeout: 25_000 });

    // 这两处表达的是「当前结论」，不允许再出现可行口径
    expect(await hero(page).innerText()).not.toContain('资金安排可行');
    expect(await planCard(page).innerText()).not.toContain('资金安排可行');
  });
});
