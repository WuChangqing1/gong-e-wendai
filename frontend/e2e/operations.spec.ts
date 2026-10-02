/**
 * 端到端验收（二）：现金事件、版本与来源、情景分析、CSV 导入。
 */

import { expect, test } from '@playwright/test';

import { btn, createMerchantFixture, gotoAuthed, loginViaUi } from './helpers';

test.describe('现金事件', () => {
  test('列表、统计与筛选', async ({ page, request }) => {
    const fixture = await createMerchantFixture(request, 'events');
    await loginViaUi(page, fixture.user.username);
    await gotoAuthed(page, '/events');

    await expect(page.getByText('商户结算款')).toBeVisible();
    await expect(page.getByText('供应商货款')).toBeVisible();
    await expect(page.getByText('平台结算款')).toBeVisible();
    await expect(page.getByText('计划中收入')).toBeVisible();

    await page.getByPlaceholder('搜索事项名称、编号、备注').fill('供应商');
    await page.keyboard.press('Enter');
    await expect(page.getByText('共 1 条')).toBeVisible({ timeout: 15_000 });
  });

  test('新增事项需经确认摘要', async ({ page, request }) => {
    const fixture = await createMerchantFixture(request, 'create');
    await loginViaUi(page, fixture.user.username);
    await gotoAuthed(page, '/events');

    await btn(page, '新增事项').click();
    await page.getByLabel('事项名称').fill('门店租金');
    await page.getByLabel('金额（元）').fill('3000');
    await btn(page, '创建事项').click();

    await expect(page.getByText('确认新增事项')).toBeVisible();
    await expect(page.getByText('¥3,000.00')).toBeVisible();
    await btn(page, '确认创建').click();
    await expect(page.getByText('事项已创建')).toBeVisible({ timeout: 15_000 });
    await expect(page.getByRole('cell', { name: '门店租金' })).toBeVisible();
  });

  test('金额错误会阻止提交', async ({ page, request }) => {
    const fixture = await createMerchantFixture(request, 'invalid');
    await loginViaUi(page, fixture.user.username);
    await gotoAuthed(page, '/events');

    await btn(page, '新增事项').click();
    await page.getByLabel('事项名称').fill('异常金额');

    // 未填写金额：必须阻止提交并给出明确提示
    await btn(page, '创建事项').click();
    await expect(page.getByText('请输入金额').first()).toBeVisible();
    await expect(page.locator('.ant-modal-title')).toBeHidden();

    // 金额为 0 同样不允许提交
    await page.getByLabel('金额（元）').fill('0');
    await btn(page, '创建事项').click();
    await expect(page.locator('.ant-form-item-explain-error').first()).toBeVisible();
    await expect(page.locator('.ant-modal-title')).toBeHidden();

    // 负号被输入框直接拒绝，无法录入负金额
    const amount = page.getByLabel('金额（元）');
    await amount.fill('500');
    await amount.press('End');
    await amount.press('-');
    await expect(amount).toHaveValue(/500/);

    // 合法金额可以进入确认摘要
    await btn(page, '创建事项').click();
    await expect(page.locator('.ant-modal-title')).toContainText('确认新增事项');
    await expect(page.getByText('¥500.00')).toBeVisible();
  });

  test('修改金额生成新版本并触发重算', async ({ page, request }) => {
    const fixture = await createMerchantFixture(request, 'revision');
    await loginViaUi(page, fixture.user.username);
    await gotoAuthed(page, '/events');

    const row = page.getByRole('row', { name: /供应商货款/ });
    await row.getByRole('button').nth(0).click();
    await page.getByLabel('金额（元）').fill('1500');
    await btn(page, '保存修改').click();
    await expect(page.getByText('确认修改内容')).toBeVisible();
    await expect(page.getByText(/保存后会保留旧版本/)).toBeVisible();
    await btn(page, '确认修改').click();
    await expect(page.getByText(/当前版本 v2/)).toBeVisible({ timeout: 15_000 });

    await gotoAuthed(page, '/today');
    await expect(page.locator('.gew-hero__amount')).toContainText('700', { timeout: 25_000 });
  });

  test('来源抽屉可追溯到原始内容', async ({ page, request }) => {
    const fixture = await createMerchantFixture(request, 'source');
    await loginViaUi(page, fixture.user.username);
    await gotoAuthed(page, '/events');

    const row = page.getByRole('row', { name: /供应商货款/ });
    await row.getByRole('button').nth(1).click();
    await expect(page.getByText('来源与追踪')).toBeVisible();
    await expect(page.getByText('来源记录').first()).toBeVisible();
    await expect(page.getByText('原始内容')).toBeVisible();
    await expect(page.getByText(/采购合同 HT-2025-018/).first()).toBeVisible();
  });

  test('版本抽屉展示历史版本但不覆盖', async ({ page, request }) => {
    const fixture = await createMerchantFixture(request, 'history');
    await loginViaUi(page, fixture.user.username);
    await gotoAuthed(page, '/events');

    const row = page.getByRole('row', { name: /供应商货款/ });
    await row.getByRole('button').nth(0).click();
    await page.getByLabel('金额（元）').fill('1500');
    await btn(page, '保存修改').click();
    await btn(page, '确认修改').click();
    await expect(page.getByText(/当前版本 v2/)).toBeVisible({ timeout: 15_000 });

    const updated = page.getByRole('row', { name: /供应商货款/ });
    await updated.getByRole('button').nth(2).click();
    await expect(page.getByText('版本历史')).toBeVisible();
    await expect(page.getByText('影响金额计算').first()).toBeVisible();
    await expect(page.getByText(/¥1,?000\.00/).first()).toBeVisible();
    await expect(page.getByText(/¥1,?500\.00/).first()).toBeVisible();
  });

  test('取消事项保留历史且不参与计算', async ({ page, request }) => {
    const fixture = await createMerchantFixture(request, 'cancel');
    await loginViaUi(page, fixture.user.username);
    await gotoAuthed(page, '/events');

    const row = page.getByRole('row', { name: /商户结算款/ });
    await row.getByRole('button').nth(3).click();
    await btn(page, '确认取消').click();
    await expect(page.getByText('事项已取消，历史记录仍然保留')).toBeVisible({ timeout: 15_000 });

    await gotoAuthed(page, '/today');
    await expect(page.locator('.gew-hero__amount')).toContainText('0', { timeout: 25_000 });
  });
});

test.describe('情景分析', () => {
  test('多情景曲线与对比明细', async ({ page, request }) => {
    const fixture = await createMerchantFixture(request, 'scenario');
    await loginViaUi(page, fixture.user.username);
    await gotoAuthed(page, '/analysis');

    await expect(page.getByText('资金曲线对比')).toBeVisible({ timeout: 25_000 });
    await expect(page.getByText('情景对比明细')).toBeVisible();
    await expect(page.getByRole('cell', { name: '按当前计划' })).toBeVisible();
    await expect(page.getByRole('cell', { name: '到账延迟' })).toBeVisible();
    await expect(page.getByText('最保守').first()).toBeVisible();
    await expect(page.getByText('风险差异（各情景可提用上限的差距）')).toBeVisible();
  });

  test('自定义情景不修改真实现金事件', async ({ page, request }) => {
    const fixture = await createMerchantFixture(request, 'custom');
    await loginViaUi(page, fixture.user.username);
    await gotoAuthed(page, '/analysis');

    await btn(page, '新建情景').click();
    await page.getByLabel('情景名称').fill('结算推迟到第 5 天');
    await page.getByLabel('要调整的事项').click();
    await page.getByTitle(/商户结算款/).click();
    await btn(page, '创建情景').click();
    await expect(page.getByText('情景已创建')).toBeVisible({ timeout: 15_000 });

    await gotoAuthed(page, '/events');
    await expect(page.getByRole('row', { name: /商户结算款/ })).toContainText('¥2,200.00');
  });
});

test.describe('CSV 导入', () => {
  test('中文表头文件走完整确认流程', async ({ page, request }) => {
    const fixture = await createMerchantFixture(request, 'import');
    await loginViaUi(page, fixture.user.username);
    await gotoAuthed(page, '/events');

    await btn(page, '导入 CSV').click();
    await page.locator('.ant-radio-button-wrapper').filter({ hasText: '未来付款计划' }).click();

    const future = new Date(Date.now() + 2 * 86_400_000).toISOString().slice(0, 10);
    await page.locator('input[type="file"]').setInputFiles({
      name: '付款计划.csv',
      mimeType: 'text/csv',
      buffer: Buffer.from(
        `编号,名称,方向,金额,预计时间,状态,来源,备注\nCSV-001,门店水电费,支出,860.00,${future} 10:00,计划中,供电局,本月账单\n`,
        'utf8',
      ),
    });
    await btn(page, '解析文件').click();

    await expect(page.getByText('字段映射', { exact: true })).toBeVisible({ timeout: 25_000 });
    await expect(page.getByText('可导入', { exact: true })).toBeVisible();
    await btn(page, '应用字段映射').click();
    await expect(page.getByText('数据预览')).toBeVisible();
    await btn(page, '下一步：预览并确认').click();
    await btn(page, '确认导入').first().click();

    await expect(page.getByText(/导入完成：新增 1 条/)).toBeVisible({ timeout: 25_000 });
    await expect(page.getByText('门店水电费')).toBeVisible();
  });

  test('缺少必要字段时禁止导入', async ({ page, request }) => {
    const fixture = await createMerchantFixture(request, 'badtemplate');
    await loginViaUi(page, fixture.user.username);
    await gotoAuthed(page, '/events');

    await btn(page, '导入 CSV').click();
    await page.locator('input[type="file"]').setInputFiles({
      name: '缺失字段.csv',
      mimeType: 'text/csv',
      buffer: Buffer.from('款项说明,收付标志\n门店租金,outflow\n', 'utf8'),
    });
    await btn(page, '解析文件').click();

    await expect(page.getByText(/缺少必要字段：/).first()).toBeVisible({ timeout: 25_000 });
    await expect(btn(page, '下一步：预览并确认')).toBeDisabled();
  });
});
