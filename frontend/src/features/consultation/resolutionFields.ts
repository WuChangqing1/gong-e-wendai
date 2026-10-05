/**
 * 咨询处理结果字段的展示模型。
 *
 * 咨询结论可以回写的事项字段是有限的（金额 / 预计时间 / 状态 / 名称 / 备注 / 来源），
 * 界面必须：
 * * 用中文标签，绝不显示 `amount_cents` 这类内部字段名；
 * * 按字段语义格式化（金额走 formatCny、时间走 formatDateTime、状态走业务标签），
 *   不出现裸分、裸 ISO 时间、英文状态码。
 */

import { formatDateTime } from '@/utils/datetime';
import {
  RESOLUTION_FIELD_LABELS,
  STATE_LABELS,
  UNKNOWN_RESOLUTION_FIELD_LABEL,
} from '@/utils/labels';
import { formatCny } from '@/utils/money';

export interface ResolutionFieldItem {
  key: string;
  label: string;
  value: string;
}

/** 把 resolution_fields 渲染成「中文标签 + 可读值」列表。 */
export function buildResolutionFieldItems(
  fields: Record<string, unknown> | null | undefined,
): ResolutionFieldItem[] {
  return Object.entries(fields ?? {})
    .filter(([, value]) => value !== null && value !== undefined && value !== '')
    .map(([key, value]) => ({
      key,
      label: RESOLUTION_FIELD_LABELS[key] ?? UNKNOWN_RESOLUTION_FIELD_LABEL,
      value: renderResolutionValue(key, value),
    }));
}

function renderResolutionValue(key: string, value: unknown): string {
  if (typeof value === 'number') {
    return key === 'amount_cents' ? formatCny(value) : String(value);
  }
  if (typeof value === 'boolean') {
    return value ? '是' : '否';
  }
  if (typeof value === 'string') {
    if (key === 'scheduled_at') return formatDateTime(value);
    if (key === 'state') return STATE_LABELS[value as keyof typeof STATE_LABELS] ?? value;
    return value;
  }
  // 结构型数据不直接展示，避免把内部对象以 JSON 形式泄露给经营者。
  return '—';
}
