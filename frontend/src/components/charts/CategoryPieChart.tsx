/**
 * 收支结构饼图（按事项类型）。
 *
 * 收入与支出分别成环，避免把两个方向的金额混在一个饼里造成误读。
 */

import { useMemo, useState } from 'react';
import { Segmented } from 'antd';
import type { EChartsOption } from 'echarts';

import ChartFrame from '@/components/charts/ChartFrame';
import { CHART_COLORS } from '@/components/charts/echarts';
import type { CategoryTerm } from '@/types';
import { formatCny, formatPercent } from '@/utils/money';

export default function CategoryPieChart({
  terms,
  height = 260,
}: {
  terms: CategoryTerm[];
  height?: number;
}) {
  const hasInflow = terms.some((item) => item.direction === 'inflow');
  const hasOutflow = terms.some((item) => item.direction === 'outflow');
  const [direction, setDirection] = useState<'inflow' | 'outflow'>(
    hasInflow ? 'inflow' : 'outflow',
  );

  const rows = useMemo(
    () =>
      terms
        .filter((item) => item.direction === direction)
        .sort((a, b) => b.amount_cents - a.amount_cents),
    [terms, direction],
  );

  const option = useMemo<EChartsOption>(() => {
    const data = rows.map((item, index) => ({
      name: item.label,
      value: item.amount_cents / 100,
      itemStyle: {
        color: direction === 'inflow' ? CHART_COLORS.soft[index % 6] : CHART_COLORS.series[index % 6],
        borderColor: '#fff',
        borderWidth: 2,
      },
      meta: item,
    }));

    return {
      tooltip: {
        trigger: 'item',
        backgroundColor: 'rgba(255,255,255,0.98)',
        borderColor: '#E8E8E8',
        borderWidth: 1,
        textStyle: { color: '#1F1F1F', fontSize: 12 },
        formatter: (params: unknown) => {
          const item = params as { name: string; value: number; percent: number; data?: { meta?: CategoryTerm } };
          const meta = item.data?.meta;
          return [
            `<b>${item.name}</b>`,
            `${formatCny(Math.round(item.value * 100))}（${item.percent}%）`,
            meta ? `${meta.event_count} 笔` : '',
          ]
            .filter(Boolean)
            .join('<br/>');
        },
      },
      series: [
        {
          type: 'pie',
          radius: ['46%', '72%'],
          center: ['50%', '52%'],
          avoidLabelOverlap: true,
          itemStyle: { borderRadius: 4 },
          label: {
            color: '#1F1F1F',
            fontSize: 12,
            formatter: '{b}\n{d}%',
            lineHeight: 16,
          },
          labelLine: { length: 8, length2: 10, lineStyle: { color: '#D9D9D9' } },
          data,
        },
      ],
      legend: { show: false },
    };
  }, [rows, direction]);

  const total = rows.reduce((sum, item) => sum + item.amount_cents, 0);

  return (
    <div>
      {hasInflow && hasOutflow ? (
        <div style={{ marginBottom: 8 }}>
          <Segmented
            size="small"
            value={direction}
            onChange={(value) => setDirection(value as 'inflow' | 'outflow')}
            options={[
              { label: '收入结构', value: 'inflow' },
              { label: '支出结构', value: 'outflow' },
            ]}
          />
        </div>
      ) : null}
      <ChartFrame
        option={option}
        height={height}
        isEmpty={rows.length === 0}
        emptyHint={
          direction === 'inflow' ? '未来 7 天没有计划收入' : '未来 7 天没有计划支出'
        }
        ariaLabel="按事项类型的收支结构饼图"
        footer={
          <>
            {rows.map((item, index) => (
              <span className="gew-chart__legend-item" key={`${item.event_type}-${index}`}>
                <span
                  className="gew-chart__legend-swatch"
                  style={{
                    background:
                      direction === 'inflow'
                        ? CHART_COLORS.soft[index % 6]
                        : CHART_COLORS.series[index % 6],
                  }}
                  aria-hidden="true"
                />
                {item.label}
                <span style={{ color: 'var(--text-muted)' }}>
                  {formatCny(item.amount_cents)}（{formatPercent(item.share_ratio, 0)}）
                </span>
              </span>
            ))}
            {rows.length > 0 ? (
              <span className="gew-chart__legend-item" style={{ color: 'var(--text-secondary)' }}>
                合计 <span className="num">{formatCny(total)}</span>
              </span>
            ) : null}
          </>
        }
      />
    </div>
  );
}
