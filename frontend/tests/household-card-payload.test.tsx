/**
 * 家庭协同卡渲染模型测试。
 *
 * 目标：分享预览与接收端共用同一套标签/格式，界面永不出现内部字段名。
 */

import { describe, expect, it } from 'vitest';

import { SHARE_FIELD_LABELS, STATUS_LABELS } from '@/utils/labels';
import {
  CARD_PAYLOAD_LABELS,
  UNKNOWN_PAYLOAD_LABEL,
  buildCardPayloadItems,
} from '@/features/household/cardPayload';

/** 与后端 household_service.SHAREABLE_FIELDS 逐项一致。 */
const SHAREABLE_FIELDS = [
  'max_withdrawable',
  'planned_amount',
  'limiting_point',
  'limiting_balance',
  'end_balance',
  'key_payments',
  'risk_summary',
  'payment_gap',
  'buffer_gap',
  'pending_inflows',
  'revision_summary',
];

/** 与后端 household_service.SHARED_FIELD_OUTPUTS 的产物一致。 */
const PAYLOAD_KEYS = [
  'max_withdrawable_cents',
  'planned_household_amount_cents',
  'limiting_timestamp',
  'limiting_balance_cents',
  'end_balance_cents',
  'key_payments',
  'limiting_event_title',
  'risk_summary',
  'status',
  'payment_gap_cents',
  'buffer_gap_cents',
  'pending_inflows',
  'revision_summary',
];

describe('分享字段标签', () => {
  it('后端白名单里的每个字段都有中文标签', () => {
    const missing = SHAREABLE_FIELDS.filter((field) => !SHARE_FIELD_LABELS[field]);
    expect(missing).toEqual([]);
  });

  it('标签里不出现内部字段名', () => {
    for (const label of Object.values(SHARE_FIELD_LABELS)) {
      expect(label).not.toMatch(/[a-z_]{4,}/i);
    }
  });
});

describe('协同卡 payload 渲染', () => {
  it('每个 payload 键都有中文标签', () => {
    const missing = PAYLOAD_KEYS.filter((key) => !CARD_PAYLOAD_LABELS[key]);
    expect(missing).toEqual([]);
  });

  it('未登记的键显示「其他信息」而不是字段名', () => {
    const items = buildCardPayloadItems({ some_internal_key: 'x' });
    expect(items).toHaveLength(1);
    expect(items[0].label).toBe(UNKNOWN_PAYLOAD_LABEL);
    expect(items[0].label).not.toContain('some_internal_key');
  });

  it('金额、时间与状态都按业务口径渲染', async () => {
    const { render, screen } = await import('@testing-library/react');
    const items = buildCardPayloadItems({
      max_withdrawable_cents: 120_000,
      payment_gap_cents: 20_000,
      limiting_timestamp: '2026-10-07T03:00:00+00:00',
      status: 'PAYMENT_GAP',
      risk_summary: '结算晚到两天后仍能覆盖所有已确认付款。',
      pending_inflows: [{ title: '平台结算款', amount_text: '¥2,000.00' }],
    });

    render(
      <div>
        {items.map((item) => (
          <div key={item.key}>
            <span>{item.label}</span>
            <span>{item.value}</span>
          </div>
        ))}
      </div>,
    );

    expect(screen.getByText('¥1,200.00')).toBeInTheDocument();
    expect(screen.getByText('¥200.00')).toBeInTheDocument();
    expect(screen.getByText(STATUS_LABELS.PAYMENT_GAP)).toBeInTheDocument();
    expect(screen.getByText('平台结算款 ¥2,000.00')).toBeInTheDocument();
    // 渲染结果里不应出现任何内部字段名
    const text = document.body.textContent ?? '';
    expect(text).not.toMatch(/_cents|limiting_timestamp|status/);
  });

  it('结构型数据不会以 JSON 形式泄露', () => {
    const items = buildCardPayloadItems({ risk_summary: { nested: true } });
    expect(items[0].value).toBe('—');
  });

  it('更正通知卡渲染「改前 → 改后」，且不显示内部字段名', async () => {
    const { render, screen } = await import('@testing-library/react');
    const items = buildCardPayloadItems({
      revision_summary: {
        event_title: '鲜食原料采购',
        version: 3,
        changed_at: '2026-10-04T01:00:00+00:00',
        change_reason: 'product-data: 按实际单据修正',
        changes: [
          {
            field: 'amount_cents',
            label: '金额',
            before: 130_000,
            after: 140_000,
            before_text: '¥1,300.00',
            after_text: '¥1,400.00',
          },
        ],
      },
    });

    render(
      <div>
        {items.map((item) => (
          <div key={item.key}>
            <span>{item.label}</span>
            <span>{item.value}</span>
          </div>
        ))}
      </div>,
    );

    expect(screen.getByText('事项变更摘要')).toBeInTheDocument();
    expect(screen.getByText('事项：鲜食原料采购')).toBeInTheDocument();
    expect(screen.getByText('金额')).toBeInTheDocument();
    expect(screen.getByText('¥1,300.00')).toBeInTheDocument();
    expect(screen.getByText('¥1,400.00')).toBeInTheDocument();
    // 只显示中文标签与可读文本，内部字段名不进入界面
    expect(document.body.textContent ?? '').not.toContain('amount_cents');
  });

  it('没有真实差异时不渲染空的更正摘要', async () => {
    const { render } = await import('@testing-library/react');
    const items = buildCardPayloadItems({
      revision_summary: { event_title: '鲜食原料采购', version: 3, changes: [] },
    });
    // 只断言本次渲染的容器，避免受其它用例残留 DOM 影响
    const { container } = render(<div>{items[0].value}</div>);
    expect(container.textContent).not.toContain('鲜食原料采购');
    expect(container.textContent?.trim()).toBe('—');
  });
});
