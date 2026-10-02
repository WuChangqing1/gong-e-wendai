/**
 * 余额变化瀑布图：从期初余额出发，逐笔收付款累加到期末余额。
 *
 * 每个柱子表示一笔事项对余额的影响（收入向上、支出向下），
 * 最后两根为「期初余额」与「期末余额」的汇总柱。
 */

import { useMemo } from 'react';
import type { EChartsOption } from 'echarts';

import ChartFrame from '@/components/charts/ChartFrame';
import { CHART_COLORS, TOOLTIP_STYLE, yuanAxisLabel } from '@/components/charts/echarts';
import type { BalancePoint } from '@/types';
import { formatCny, formatSigned } from '@/utils/money';
import { formatShortDateTime } from '@/utils/datetime';

interface WaterfallRow {
  label: string;
  /** 柱子的可见高度（用于堆叠占位） */
  base: number;
  delta: number;
  balance: number;
  title: string;
  direction: 'inflow' | 'outflow' | null;
}

export default function BalanceWaterfallChart({
  points,
  height = 280,
}: {
  points: BalancePoint[];
  height?: number;
}) {
  const rows = useMemo<WaterfallRow[]>(() => {
    if (points.length === 0) return [];
    const opening = points[0];
    const result: WaterfallRow[] = [
      {
        label: '期初',
        base: 0,
        delta: opening.balance_cents / 100,
        balance: opening.balance_cents,
        title: '期初余额',
        direction: null,
      },
    ];
    for (const point of points.slice(1)) {
      const previous = point.balance_cents - point.delta_cents;
      const delta = point.delta_cents / 100;
      result.push({
        label: point.event_title.length > 6 ? `${point.event_title.slice(0, 6)}…` : point.event_title,
        base: (delta >= 0 ? previous : point.balance_cents) / 100,
        delta: Math.abs(delta),
        balance: point.balance_cents,
        title: point.event_title,
        direction: point.direction,
      });
    }
    const last = points[points.length - 1];
    result.push({
      label: '期末',
      base: 0,
      delta: last.balance_cents / 100,
      balance: last.balance_cents,
      title: '期末余额',
      direction: null,
    });
    return result;
  }, [points]);

  const option = useMemo<EChartsOption>(() => {
    const placeholder = rows.map((row) => row.base);
    const inflow = rows.map((row) =>
      row.direction !== 'outflow' && row.title !== '期末余额' ? row.delta : '-',
    );
    const outflow = rows.map((row) => (row.direction === 'outflow' ? row.delta : '-'));
    const totals = rows.map((row) =>
      row.title === '期末余额' ? row.delta : '-',
    );

    const labelFor = (index: number, kind: 'in' | 'out' | 'total'): string => {
      const row = rows[index];
      if (!row) return '';
      if (kind === 'total') return formatCny(row.balance);
      return formatSigned(Math.round(row.delta * 100));
    };

    return {
      grid: { left: 8, right: 16, top: 28, bottom: 8, containLabel: true },
      tooltip: {
        ...TOOLTIP_STYLE,
        formatter: (params: unknown) => {
          const list = Array.isArray(params) ? params : [params];
          const index = (list[0] as { dataIndex?: number })?.dataIndex ?? 0;
          const row = rows[index];
          if (!row) return '';
          const lines = [`<b>${row.title}</b>`];
          if (row.direction) lines.push(`本笔变化 ${formatSigned(Math.round(row.delta * 100))}`);
          lines.push(`该笔后余额 ${formatCny(row.balance)}`);
          return lines.join('<br/>');
        },
      },
      xAxis: {
        type: 'category',
        data: rows.map((row) => row.label),
        axisTick: { show: false },
        axisLine: { lineStyle: { color: '#E8E8E8' } },
        axisLabel: { color: '#999', fontSize: 11, interval: 0, rotate: rows.length > 6 ? 30 : 0 },
      },
      yAxis: {
        type: 'value',
        axisLabel: { color: '#999', fontSize: 11, formatter: yuanAxisLabel },
        splitLine: { lineStyle: { color: '#F0F0F0' } },
      },
      series: [
        {
          name: '占位',
          type: 'bar',
          stack: 'waterfall',
          silent: true,
          itemStyle: { color: 'transparent' },
          emphasis: { itemStyle: { color: 'transparent' } },
          data: placeholder,
        },
        {
          name: '收入',
          type: 'bar',
          stack: 'waterfall',
          barMaxWidth: 26,
          itemStyle: { color: CHART_COLORS.inflow },
          label: {
            show: true,
            position: 'top',
            fontSize: 10,
            color: CHART_COLORS.inflow,
            formatter: (params: unknown) => labelFor((params as { dataIndex: number }).dataIndex, 'in'),
          },
          data: inflow,
        },
        {
          name: '支出',
          type: 'bar',
          stack: 'waterfall',
          barMaxWidth: 26,
          itemStyle: { color: CHART_COLORS.outflow },
          label: {
            show: true,
            position: 'bottom',
            fontSize: 10,
            color: CHART_COLORS.outflow,
            formatter: (params: unknown) => labelFor((params as { dataIndex: number }).dataIndex, 'out'),
          },
          data: outflow,
        },
        {
          name: '汇总',
          type: 'bar',
          stack: 'waterfall',
          barMaxWidth: 26,
          itemStyle: { color: CHART_COLORS.series[1] },
          label: {
            show: true,
            position: 'top',
            fontSize: 10,
            color: CHART_COLORS.series[1],
            formatter: (params: unknown) =>
              labelFor((params as { dataIndex: number }).dataIndex, 'total'),
          },
          data: totals,
        },
        {
          name: '留底线',
          type: 'line',
          silent: true,
          symbol: 'none',
          lineStyle: { color: CHART_COLORS.buffer, type: 'dashed', width: 1 },
          data: [],
          markLine: undefined,
        },
      ],
    };
  }, [rows]);

  return (
    <ChartFrame
      option={option}
      height={height}
      isEmpty={rows.length <= 2}
      emptyHint="未来 7 天没有已确认的收付款事项，暂不生成余额变化"
      ariaLabel="余额变化瀑布图"
      footer={
        <>
          <span className="gew-chart__legend-item">
            <span
              className="gew-chart__legend-swatch"
              style={{ background: CHART_COLORS.inflow }}
              aria-hidden="true"
            />
            收入增加
          </span>
          <span className="gew-chart__legend-item">
            <span
              className="gew-chart__legend-swatch"
              style={{ background: CHART_COLORS.outflow }}
              aria-hidden="true"
            />
            支出减少
          </span>
          <span className="gew-chart__legend-item">
            <span
              className="gew-chart__legend-swatch"
              style={{ background: CHART_COLORS.series[1] }}
              aria-hidden="true"
            />
            期初 / 期末
          </span>
          {points.length > 1 ? (
            <span className="gew-chart__legend-item" style={{ color: 'var(--text-muted)' }}>
              共 {points.length - 1} 笔 · 最后时点 {formatShortDateTime(points[points.length - 1].timestamp)}
            </span>
          ) : null}
        </>
      }
    />
  );
}
