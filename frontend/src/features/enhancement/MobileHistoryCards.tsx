/**
 * 手机端的历史经营数据分组卡片。
 *
 * 桌面端用表格展示「日期 / 收入 / 支出 / 净额 / 完整性」，
 * 手机上按日期分组为卡片，数字仍然保留分位格式，避免横向拖动。
 */

import { Empty } from 'antd';

import type { DailyHistoryRow } from '@/api/enhancements';
import { formatCny, formatSigned } from '@/utils/money';

export interface MobileHistoryCardsProps {
  items: DailyHistoryRow[];
  emptyHint?: string;
}

/** 2026-10-03 → 10月3日 */
function chineseDay(day: string): string {
  const [, month, date] = day.split('-');
  if (!month || !date) return day;
  return `${Number(month)}月${Number(date)}日`;
}

export default function MobileHistoryCards({
  items,
  emptyHint = '还没有已确认完整的历史经营数据',
}: MobileHistoryCardsProps) {
  if (items.length === 0) {
    return <Empty description={emptyHint} image={Empty.PRESENTED_IMAGE_SIMPLE} />;
  }

  return (
    <ul className="gew-history-cards" data-testid="history-card-list">
      {items.map((row) => (
        <li className="gew-history-card" key={row.id ?? row.day}>
          <div className="gew-history-card__day">{chineseDay(row.day)}</div>

          <div className="gew-history-card__row">
            <span className="gew-history-card__label">日常到账</span>
            <span className="num gew-amount-inflow">{formatCny(row.inflow_cents)}</span>
          </div>
          <div className="gew-history-card__row">
            <span className="gew-history-card__label">日常采购</span>
            <span className="num">{formatCny(row.outflow_cents)}</span>
          </div>
          <div className="gew-history-card__row">
            <span className="gew-history-card__label">净变化</span>
            <span className="num">{formatSigned(row.net_cents)}</span>
          </div>

          <div className="gew-history-card__row">
            <span className="gew-history-card__label">数据完整性</span>
            <span>{row.completeness_confirmed ? '已确认完整' : '按记录导入'}</span>
          </div>
        </li>
      ))}
    </ul>
  );
}
