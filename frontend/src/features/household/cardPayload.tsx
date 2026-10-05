/**
 * 家庭协同卡 payload 的统一渲染模型。
 *
 * 分享预览（经营者确认分享前看到的）与接收端（家人看到的）**必须共用本模块**，
 * 这样「勾了什么」与「看到什么」才逐项一致：
 *
 * * 每个 payload 键都有中文标签，未登记的键统一显示「其他信息」，
 *   绝不把 `revision_summary` 这类内部字段名渲染给用户；
 * * 金额一律走 `formatCny`，时间一律走 `formatDateTime`，状态走业务标签，
 *   不出现 JSON、原始小数或英文状态码。
 */

import type { ReactNode } from 'react';

import RevisionSummary from '@/features/household/RevisionSummary';
import { formatDateTime } from '@/utils/datetime';
import { STATUS_LABELS } from '@/utils/labels';
import { formatCny } from '@/utils/money';

/** payload 键 → 用户可读标签。新增字段必须同步登记。 */
export const CARD_PAYLOAD_LABELS: Record<string, string> = {
  max_withdrawable_cents: '今日可提用金额',
  planned_household_amount_cents: '计划家庭提用金额',
  limiting_timestamp: '最紧张时间',
  limiting_balance_cents: '最紧时点余额',
  end_balance_cents: '期末余额',
  limiting_event_title: '关键付款',
  key_payments: '关键经营付款',
  risk_summary: '风险摘要',
  status: '风险状态',
  payment_gap_cents: '付款缺口',
  buffer_gap_cents: '留底缺口',
  pending_inflows: '尚未到账的收入',
  revision_summary: '事项变更摘要',
};

/** 未登记键的统一标签：宁可少说，也不暴露内部字段名。 */
export const UNKNOWN_PAYLOAD_LABEL = '其他信息';

/** 金额键：统一按人民币展示。 */
const MONEY_KEYS = new Set([
  'max_withdrawable_cents',
  'planned_household_amount_cents',
  'limiting_balance_cents',
  'end_balance_cents',
  'payment_gap_cents',
  'buffer_gap_cents',
]);

/** 缺口键：大于 0 时是需要提醒的风险值。 */
const GAP_KEYS = new Set(['payment_gap_cents', 'buffer_gap_cents']);

export interface CardPayloadItem {
  key: string;
  label: string;
  value: ReactNode;
}

function renderValue(key: string, value: unknown): ReactNode {
  if (value === null || value === undefined) return '—';

  if (key === 'revision_summary' && typeof value === 'object' && !Array.isArray(value)) {
    return <RevisionSummary data={value as Record<string, unknown>} />;
  }

  if (typeof value === 'number') {
    if (!MONEY_KEYS.has(key)) return String(value);
    const danger = GAP_KEYS.has(key) && value > 0;
    return (
      <span className="num" style={{ color: danger ? 'var(--danger)' : undefined }}>
        {formatCny(value)}
      </span>
    );
  }

  if (typeof value === 'string') {
    if (key === 'limiting_timestamp') return formatDateTime(value);
    if (key === 'status') return STATUS_LABELS[value as keyof typeof STATUS_LABELS] ?? '—';
    return value;
  }

  if (Array.isArray(value)) {
    // 空列表必须有明确空状态，不能留一片空白（例如「尚未到账的收入」）
    if (value.length === 0) return '暂无';
    return (
      <ul style={{ margin: 0, paddingLeft: 18 }}>
        {value.map((item, index) => (
          <li key={index}>
            {typeof item === 'object' && item !== null && 'title' in item
              ? `${String((item as { title: unknown }).title)} ${
                  (item as { amount_text?: string }).amount_text ?? ''
                }`.trim()
              : String(item)}
          </li>
        ))}
      </ul>
    );
  }

  // 结构型数据不直接展示，避免把内部对象以 JSON 形式泄露给家人。
  return '—';
}

/** 把共享 payload 渲染成「标签 + 值」列表。 */
export function buildCardPayloadItems(
  payload: Record<string, unknown> | null | undefined,
): CardPayloadItem[] {
  return Object.entries(payload ?? {}).map(([key, value]) => ({
    key,
    label: CARD_PAYLOAD_LABELS[key] ?? UNKNOWN_PAYLOAD_LABEL,
    value: renderValue(key, value),
  }));
}
