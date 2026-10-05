/**
 * 家庭分享抽屉的边界验收。
 *
 * 重点：勾了「尚未到账的收入」但当前确实没有这类收入时，
 * 预览必须给出明确空状态，并且不允许发出这张没有内容的卡片
 * （后端也会拒绝，前端禁用只是体验）。
 */

import { expect, test } from '@playwright/test';

import {
  PASSWORD,
  apiUrl,
  btn,
  createMerchantFixture,
  gotoAuthed,
  loginViaUi,
  uniqueName,
} from './helpers';

const CSRF = { 'X-Requested-With': 'XMLHttpRequest' };

test.describe('家庭分享边界', () => {
  test('没有尚未到账的收入时给出空状态且不能分享', async ({ page, request, playwright }) => {
    const fixture = await createMerchantFixture(request, 'nopending');

    // 把唯一一笔计划收入取消：此时「尚未到账的收入」为空
    const cancel = await request.post(
      apiUrl('cash-events', fixture.eventIds['E2E-SETTLE-0001'], 'cancel'),
      { headers: CSRF, data: { reason: '改用其它结算方式' } },
    );
    expect(cancel.status(), await cancel.text()).toBe(200);

    // 建家庭并放行一位家庭成员，避免按钮因「没有可分享对象」而禁用。
    // 家庭成员用一个独立上下文，免得把经营者的会话顶掉。
    const household = await request.post(apiUrl('households'), {
      headers: CSRF,
      data: { name: '端到端之家' },
    });
    expect(household.status(), await household.text()).toBe(201);
    const code = (await household.json()).invite_code as string;

    const memberContext = await playwright.request.newContext();
    try {
      const memberName = uniqueName('member');
      const member = await memberContext.post(apiUrl('auth', 'register'), {
        headers: CSRF,
        data: {
          username: memberName,
          password: PASSWORD,
          display_name: '家庭成员小王',
          roles: ['family_member'],
        },
      });
      expect(member.status(), await member.text()).toBe(201);
      const join = await memberContext.post(apiUrl('households', 'join'), {
        headers: CSRF,
        data: { invite_code: code, relation_label: '配偶' },
      });
      expect(join.status(), await join.text()).toBe(200);
      const membershipId = (await join.json()).membership_id as string;

      const approve = await request.post(
        apiUrl('households', 'members', membershipId, 'approve'),
        { headers: CSRF, data: {} },
      );
      expect(approve.status(), await approve.text()).toBe(200);
    } finally {
      await memberContext.dispose();
    }

    await loginViaUi(page, fixture.user.username);
    await gotoAuthed(page, '/today');
    await btn(page, '分享给家庭').click();

    const drawer = page.locator('.ant-drawer-body');
    await expect(drawer.getByText('选择要共享的内容')).toBeVisible({ timeout: 25_000 });

    const pending = drawer.getByRole('checkbox', { name: '尚未到账的收入' });
    await pending.check();

    // 预览给出明确空状态，而不是一片空白
    await expect(
      drawer.locator('.gew-kv-list__row').filter({ hasText: '尚未到账的收入' }),
    ).toContainText('暂无', { timeout: 20_000 });

    // 确认分享被禁用，并说明原因
    const confirm = btn(page, '确认分享');
    await expect(confirm).toBeDisabled();
    await confirm.hover({ force: true });
    await expect(
      page.getByText('当前没有尚未到账的收入，请取消该项后再分享。').first(),
    ).toBeVisible({ timeout: 15_000 });

    // 取消勾选后恢复正常
    await pending.uncheck();
    await expect(confirm).toBeEnabled({ timeout: 20_000 });
    await expect(drawer.locator('.gew-kv-list__row').filter({ hasText: '尚未到账的收入' })).toHaveCount(0);
  });
});
