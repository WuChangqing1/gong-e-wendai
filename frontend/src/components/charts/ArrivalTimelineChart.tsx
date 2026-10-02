/**
 * 待结算资金到账时间分布图。
 *
 * 横轴为窗口内的每一个日期，纵轴为当天预计到账金额；
 * 没有到账安排的日期显式显示为 0，便于看出「哪几天没有资金进账」。
 */

import { useMemo } from 'react';
import type { EChartsOption } from 'echarts';

import ChartFrame from '@/components/charts/ChartFrame';
import { CHART_COLORS, TOOLTIP_STYLE, yuanAxisLabel } from '@/components/charts/echarts';
import type { ArrivalTerm, DailyTerm } from '@/types';
import { formatCny } from '@/utils/money';
import { formatChineseDate, formatWeekday } from '@/utils/datetime';

export default function ArrivalTimelineChart({
  arrivals,
  dailyTerms,
  height = 240,
}: {
  arrivals: ArrivalTerm[];
  /** 用于补齐没有到账安排的日期 */
  dailyTerms: DailyTerm[];
  height?: number;
}) {
  const rows = useMemo(() => {
    const map = new Map(arrivals.map((item) => [item.day, item]));
    const days = dailyTerms.length
      ? dailyTerms.map((item) => item.day)
      : arrivals.map((item) => item.day);
    return days.map((day) => ({
      day,
      amount: map.get(day)?.amount_cents ?? 0,
      count: map.get(day)?.event_count ?? 0,
      titles: map.get(day)?.titles ?? [],
    }));
  }, [arrivals, dailyTerms]);

  const option = useMemo<EChartsOption>(() => {
    const labels = rows.map((row) => `${formatChineseDate(row.day)}\n${formatWeekday(row.day)}`);
    const values = rows.map((row) => row.amount / 100);

    return {
      grid: { left: 8, right: 16, top: 24, bottom: 8, containLabel: true },
      tooltip: {
        ...TOOLTIP_STYLE,
        formatter: (params: unknown) => {
          const list = Array.isArray(params) ? params : [params];
          const index = (list[0] as { dataIndex?: number })?.dataIndex ?? 0;
          const row = rows[index];
          if (!row) return '';
          if (row.amount === 0) return `${row.day}<br/>当天没有待结算资金到账`;
          return [
            `<div style="font-size:12px;color:#666;margin-bottom:4px">${row.day}</div>`,
            `到账金额 <b style="color:${CHART_COLORS.inflow}">${formatCny(row.amount)}</b>`,
            `${row.count} 笔`,
            row.titles.length ? row.titles.join('、') : '',
          ]
            .filter(Boolean)
            .join('<br/>');
        },
      },
      xAxis: {
        type: 'category',
        data: labels,
        axisTick: { show: false },
        axisLine: { lineStyle: { color: '#E8E8E8' } },
        axisLabel: { color: '#999', fontSize: 11, lineHeight: 14 },
      },
      yAxis: {
        type: 'value',
        axisLabel: { color: '#999', fontSize: 11, formatter: yuanAxisLabel },
        splitLine: { lineStyle: { color: '#F0F0F0' } },
      },
      series: [
        {
          name: '预计到账',
          type: 'bar',
          barMaxWidth: 26,
          itemStyle: { color: CHART_COLORS.inflow, borderRadius: [3, 3, 0, 0] },
          label: {
            show: true,
            position: 'top',
            fontSize: 10,
            color: 'var(--text-secondary)',
            formatter: (params: unknown) => {
              const value = (params as { value: number }).value;
              return value > 0 ? `${value.toLocaleString('zh-CN')}` : '';
            },
          },
          data: values,
        },
      ],
      markLine: undefined,
    };
  }, [rows]);

  const total = rows.reduce((sum, row) => sum + row.amount, 0);
  const daysWithoutArrival = rows.filter((row) => row.amount === 0).length;

  return (
    <ChartFrame
      option={option}
      height={height}
      isEmpty={total === 0}
      emptyHint="未来 7 天没有待结算资金的到账安排"
      ariaLabel="待结算资金到账时间分布图"
      footer={
        <>
          <span className="gew-chart__legend-item" style={{ color: 'var(--text-secondary)' }}>
            待结算合计 <span className="num">{formatCny(total)}</span>
          </span>
          <span className="gew-chart__legend-item" style={{ color: 'var(--text-muted)' }}>
            其中 {daysWithoutArrival} 天没有到账安排
          </span>
        </>
      }
    />
  );
}
