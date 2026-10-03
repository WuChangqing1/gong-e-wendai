/**
 * 端到端验收（三）：家庭协同、经营咨询、响应式。
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
  provisionConsultant,
  uniqueName,
} from './helpers';

test.describe('家庭协同', () => {
  test('创建家庭、生成邀请码、分享决策卡并接收反馈', async ({ page, request, browser }) => {
    const fixture = await createMerchantFixture(request, 'family');

    const memberRequest = request;
    const memberName = uniqueName('member');
    const memberRegister = await memberRequest.post(apiUrl('auth', 'register'), {
      headers: { 'X-Requested-With': 'XMLHttpRequest' },
      data: {
        username: memberName,
        password: PASSWORD,
        display_name: '家庭成员小王',
        roles: ['family_member'],
      },
    });
    expect(memberRegister.status(), await memberRegister.text()).toBe(201);

    await loginViaUi(page, fixture.user.username);
    await gotoAuthed(page, '/family');
    await btn(page, '创建家庭').click();
    await page.getByLabel('家庭名称').fill('端到端之家');
    await btn(page.locator('.ant-modal-footer'), '创建').click();
    await expect(page.getByText('家庭已创建，请把邀请码发给家人')).toBeVisible({ timeout: 15_000 });

    const inviteCode = (
      await page.locator('span.num').filter({ hasText: /^[A-Z0-9]{8}$/ }).first().innerText()
    ).trim();
    expect(inviteCode).toMatch(/^[A-Z0-9]{8}$/);

    const join = await memberRequest.post(apiUrl('households', 'join'), {
      headers: { 'X-Requested-With': 'XMLHttpRequest' },
      data: { invite_code: inviteCode, relation_label: '配偶' },
    });
    expect(join.status(), await join.text()).toBe(200);

    await page.reload();
    await btn(page, '通过申请').click();
    await expect(page.getByText('已通过加入申请')).toBeVisible({ timeout: 15_000 });

    await btn(page, '分享决策卡').click();
    await expect(page.getByText('分享预览')).toBeVisible();
    await expect(page.getByText('分享对象（已加入的家庭成员）')).toBeVisible();
    await expect(page.locator('.gew-desc-item__value').filter({ hasText: '家庭成员小王' })).toBeVisible();
    await expect(page.getByText(/以下内容默认不分享：经营账户完整余额/)).toBeVisible();
    await btn(page, '确认分享').click();
    await expect(page.getByText('已分享给家庭成员')).toBeVisible({ timeout: 15_000 });

    const memberPage = await browser.newPage();
    await loginViaUi(memberPage, memberName);
    await memberPage.goto(appUrl('/family/cards'));
    await expect(memberPage.getByText('家庭提用决策确认')).toBeVisible({ timeout: 25_000 });
    await expect(memberPage.getByText('今日可提用金额').first()).toBeVisible();
    await expect(memberPage.getByText('¥1,200.00').first()).toBeVisible();

    await btn(memberPage, '需要商量').click();
    await expect(memberPage.getByText('已记录你的反馈')).toBeVisible({ timeout: 15_000 });
    await memberPage.getByPlaceholder('写下你的意见（仅针对这张卡片）').fill('这个月还有学费要交');
    await btn(memberPage, '评论').click();
    await expect(memberPage.getByText('评论已提交')).toBeVisible({ timeout: 15_000 });

    await memberPage.goto(appUrl('/events'));
    await expect(memberPage.getByText('没有访问权限')).toBeVisible({ timeout: 25_000 });

    await memberPage.close();
  });
});

test.describe('经营咨询', () => {
  test('商户发起咨询', async ({ page, request }) => {
    const fixture = await createMerchantFixture(request, 'consult');
    await loginViaUi(page, fixture.user.username);
    await gotoAuthed(page, '/consultations');

    await btn(page, '发起咨询').click();
    await page.getByLabel('选择要咨询的收付款事项').click();
    await page.getByTitle(/结算款/).click();
    await page.getByLabel('问题类型').click();
    await page.getByTitle('到账/结算时间不明确').click();
    await page
      .getByLabel('你的问题')
      .fill('这笔 2358.60 元的结算款原定 10 月 3 日到账，但账户还没有收到，想确认结算进度。');
    await expect(page.getByText(/智能整理只使用允许共享的字段/)).toBeVisible();
    await btn(page, '提交咨询').click();
    await expect(page.getByText('咨询已提交，咨询人员会尽快受理')).toBeVisible({ timeout: 15_000 });
  });

  test('咨询人员受理核实，且无法访问经营数据', async ({ page, request }) => {
    // 咨询人员不能自助注册，也没有管理员后台可以开通。
    // V3 起统一走与生产一致的开通脚本：scripts/provision_consultant.py。
    const fixture = await createMerchantFixture(request, 'consultflow');
    const merchantContext = request;
    const loginResponse = await merchantContext.post(apiUrl('auth', 'login'), {
      headers: { 'X-Requested-With': 'XMLHttpRequest' },
      data: { username: fixture.user.username, password: PASSWORD },
    });
    expect(loginResponse.status()).toBe(200);
    const caseResponse = await merchantContext.post(apiUrl('consultations'), {
      headers: { 'X-Requested-With': 'XMLHttpRequest' },
      data: {
        cash_event_id: fixture.eventIds['E2E-SETTLE-0001'],
        question_type: 'settlement_time',
        question: '想确认这笔结算的到账进度。',
        status: 'submitted',
      },
    });
    expect(caseResponse.status(), await caseResponse.text()).toBe(201);

    const consultant = provisionConsultant(uniqueName('consultant'));

    await loginViaUi(page, consultant.username, consultant.password);
    await expect(page).toHaveURL(/\/consultant/, { timeout: 25_000 });
    await expect(page.getByRole('heading', { name: '咨询工作台' })).toBeVisible();
    await btn(page, '查看详情').first().click();

    const drawer = page.locator('.ant-drawer-body');
    await expect(page.getByText(/商户授权可见的字段/)).toBeVisible({ timeout: 25_000 });
    await expect(page.getByText('商户问题')).toBeVisible();
    // 咨询人员看不到经营余额与可提用金额
    await expect(page.getByText('今日可提用')).toBeHidden();

    await btn(drawer, '受理并开始处理').click();
    await expect(page.getByText('已受理该事项')).toBeVisible({ timeout: 15_000 });
    await expect(page.locator('.ant-drawer-body')).toContainText('处理中', { timeout: 15_000 });

    await page.getByPlaceholder(/请提供该笔结算的结算单截图编号/).fill('请提供结算批次号。');
    await btn(drawer, '提交补充要求').click();
    await expect(page.getByText('已要求商户补充资料')).toBeVisible({ timeout: 15_000 });
    await expect(page.locator('.ant-drawer-body')).toContainText('待补充资料', { timeout: 15_000 });

    await btn(drawer, '商户已补充，继续处理').click();
    await expect(page.getByText('已受理该事项')).toBeVisible({ timeout: 15_000 });
    await page.getByPlaceholder(/该笔结算已于 10 月 3 日/).fill('该笔结算已于 10 月 3 日完成。');
    await btn(drawer, '标记为已核实').click();
    await expect(page.getByText('已填写核实结果，等待商户确认更正')).toBeVisible({ timeout: 15_000 });

    await page.goto(appUrl('/events'));
    await expect(page.getByText('没有访问权限')).toBeVisible({ timeout: 25_000 });

  });
});
