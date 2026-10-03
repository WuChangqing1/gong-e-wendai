/**
 * 每日收入 / 支出对比柱状图 + 净额折线。
 *
 * 柱：当日计划收入与支出（元）
 * 线：当日净额（收入 − 支出）
 */

import { useMemo } from 'react';
import type { EChartsOption } from 'echarts';

import ChartFrame from '@/components/charts/ChartFrame';
import {
  CHART_COLORS,
  MOBILE_AXIS_LABEL,
  MOBILE_GRID,
  TOOLTIP_STYLE,
  yuanAxisLabel,
} from '@/components/charts/echarts';
import { useIsMobile } from '@/hooks/useResponsive';
import type { DailyTerm } from '@/types';
import { formatCny, formatSigned } from '@/utils/money';
import { formatChineseDate, formatCompactDay, formatWeekday } from '@/utils/datetime';

export default function DailyFlowChart({
  terms,
  height = 260,
}: {
  terms: DailyTerm[];
  height?: number;
}) {
  const isMobile = useIsMobile();

  const option = useMemo<EChartsOption>(() => {
    // 手机端只显示日期（10/3）：窄屏放不下「10月3日 + 周六」两行标签。
    const labels = terms.map((item) =>
      isMobile
        ? formatCompactDay(item.day)
        : `${formatChineseDate(item.day)}\n${formatWeekday(item.day)}`,
    );
    const inflow = terms.map((item) => item.inflow_cents / 100);
    const outflow = terms.map((item) => item.outflow_cents / 100);
    const net = terms.map((item) => item.net_cents / 100);

    return {
      grid: isMobile ? MOBILE_GRID : { left: 8, right: 16, top: 32, bottom: 8, containLabel: true },
      tooltip: {
        ...TOOLTIP_STYLE,
        formatter: (params: unknown) => {
          const list = Array.isArray(params) ? params : [params];
          const index = (list[0] as { dataIndex?: number })?.dataIndex ?? 0;
          const term = terms[index];
          if (!term) return '';
          return [
            `<div style="font-size:12px;color:#666;margin-bottom:4px">${term.day} · 共 ${term.event_count} 笔</div>`,
            `收入 <b style="color:${CHART_COLORS.inflow}">${formatCny(term.inflow_cents)}</b>`,
            `支出 <b style="color:${CHART_COLORS.outflow}">${formatCny(term.outflow_cents)}</b>`,
            `当日净额 <b>${formatSigned(term.net_cents)}</b>`,
            `当日期末余额 <b>${formatCny(term.closing_balance_cents)}</b>`,
          ].join('<br/>');
        },
      },
      // 手机端图例由 ChartFrame 的 footer 承担，避免与坐标轴争抢窄屏空间。
      legend: isMobile
        ? { show: false }
        : {
            top: 0,
            right: 0,
            itemWidth: 10,
            itemHeight: 10,
            textStyle: { color: '#666', fontSize: 12 },
            data: ['当日收入', '当日支出', '当日净额'],
          },
      xAxis: {
        type: 'category',
        data: labels,
        axisTick: { show: false },
        axisLine: { lineStyle: { color: '#E8E8E8' } },
        axisLabel: isMobile
          ? MOBILE_AXIS_LABEL
          : { color: '#999', fontSize: 11, lineHeight: 14 },
      },
      yAxis: {
        type: 'value',
        axisLabel: {
          color: '#999',
          fontSize: isMobile ? 12 : 11,
          formatter: yuanAxisLabel,
        },
        splitLine: { lineStyle: { color: '#F0F0F0' } },
      },
      series: [
        {
          name: '当日收入',
          type: 'bar',
          stack: 'flow',
          barMaxWidth: 22,
          itemStyle: { color: CHART_COLORS.inflow, borderRadius: [0, 0, 0, 0] },
          data: inflow,
        },
        {
          name: '当日支出',
          type: 'bar',
          stack: 'flow',
          barMaxWidth: 22,
          itemStyle: { color: CHART_COLORS.outflow },
          data: outflow,
        },
        {
          name: '当日净额',
          type: 'line',
          smooth: false,
          symbol: 'circle',
          symbolSize: 6,
          lineStyle: { color: CHART_COLORS.series[1], width: 2 },
          itemStyle: { color: CHART_COLORS.series[1] },
          data: net,
        },
      ],
    };
  }, [terms, isMobile]);

  const total = terms.reduce((sum, item) => sum + item.event_count, 0);

  return (
    <ChartFrame
      option={option}
      height={height}
      isEmpty={total === 0}
      emptyHint="未来 7 天还没有已确认的收付款事项"
      ariaLabel="未来 7 天每日收入与支出对比柱状图"
      footer={
        <>
          <span className="gew-chart__legend-item">
            <span
              className="gew-chart__legend-swatch"
              style={{ background: CHART_COLORS.inflow }}
              aria-hidden="true"
            />
            当日收入
          </span>
          <span className="gew-chart__legend-item">
            <span
              className="gew-chart__legend-swatch"
              style={{ background: CHART_COLORS.outflow }}
              aria-hidden="true"
            />
            当日支出
          </span>
          <span className="gew-chart__legend-item">
            <span
              className="gew-chart__legend-swatch"
              style={{ background: CHART_COLORS.series[1] }}
              aria-hidden="true"
            />
            当日净额
          </span>
        </>
      }
    />
  );
}
