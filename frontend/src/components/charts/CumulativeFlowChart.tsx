/**
 * 累计流入 / 流出面积图。
 *
 * 展示未来 7 天资金积累节奏：收入随日期累积上升，支出随日期累积下降，
 * 阴影区间表示两者之间的资金净头寸变化。
 */

import { useMemo } from 'react';
import type { EChartsOption } from 'echarts';

import ChartFrame from '@/components/charts/ChartFrame';
import { CHART_COLORS, TOOLTIP_STYLE, yuanAxisLabel } from '@/components/charts/echarts';
import type { DailyTerm } from '@/types';
import { formatCny, formatSigned } from '@/utils/money';
import { formatChineseDate, formatWeekday } from '@/utils/datetime';

export default function CumulativeFlowChart({
  terms,
  height = 260,
}: {
  terms: DailyTerm[];
  height?: number;
}) {
  const series = useMemo(() => {
    let cumulativeIn = 0;
    let cumulativeOut = 0;
    return terms.map((item) => {
      cumulativeIn += item.inflow_cents;
      cumulativeOut += item.outflow_cents;
      return {
        day: item.day,
        cumulativeIn,
        cumulativeOut,
        net: cumulativeIn - cumulativeOut,
        closing: item.closing_balance_cents,
      };
    });
  }, [terms]);

  const option = useMemo<EChartsOption>(() => {
    const labels = series.map((item) => `${formatChineseDate(item.day)}\n${formatWeekday(item.day)}`);

    return {
      grid: { left: 8, right: 16, top: 32, bottom: 8, containLabel: true },
      tooltip: {
        ...TOOLTIP_STYLE,
        formatter: (params: unknown) => {
          const list = Array.isArray(params) ? params : [params];
          const index = (list[0] as { dataIndex?: number })?.dataIndex ?? 0;
          const row = series[index];
          if (!row) return '';
          return [
            `<div style="font-size:12px;color:#666;margin-bottom:4px">截至 ${row.day}</div>`,
            `累计收入 <b style="color:${CHART_COLORS.inflow}">${formatCny(row.cumulativeIn)}</b>`,
            `累计支出 <b style="color:${CHART_COLORS.outflow}">${formatCny(row.cumulativeOut)}</b>`,
            `累计净额 <b>${formatSigned(row.net)}</b>`,
            `当日期末余额 <b>${formatCny(row.closing)}</b>`,
          ].join('<br/>');
        },
      },
      legend: {
        top: 0,
        right: 0,
        itemWidth: 10,
        itemHeight: 10,
        textStyle: { color: '#666', fontSize: 12 },
        data: ['累计收入', '累计支出', '累计净额'],
      },
      xAxis: {
        type: 'category',
        boundaryGap: false,
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
          name: '累计收入',
          type: 'line',
          smooth: true,
          symbol: 'circle',
          symbolSize: 5,
          lineStyle: { color: CHART_COLORS.inflow, width: 2 },
          itemStyle: { color: CHART_COLORS.inflow },
          areaStyle: { color: 'rgba(23,138,75,0.14)' },
          data: series.map((item) => item.cumulativeIn / 100),
        },
        {
          name: '累计支出',
          type: 'line',
          smooth: true,
          symbol: 'circle',
          symbolSize: 5,
          lineStyle: { color: CHART_COLORS.outflow, width: 2 },
          itemStyle: { color: CHART_COLORS.outflow },
          areaStyle: { color: 'rgba(217,0,0,0.10)' },
          data: series.map((item) => item.cumulativeOut / 100),
        },
        {
          name: '累计净额',
          type: 'line',
          smooth: true,
          symbol: 'none',
          lineStyle: { color: CHART_COLORS.series[1], width: 2, type: 'dashed' },
          itemStyle: { color: CHART_COLORS.series[1] },
          data: series.map((item) => item.net / 100),
        },
      ],
    };
  }, [series]);

  const hasData = series.some((item) => item.cumulativeIn !== 0 || item.cumulativeOut !== 0);

  return (
    <ChartFrame
      option={option}
      height={height}
      isEmpty={!hasData}
      emptyHint="未来 7 天还没有已确认的收付款事项"
      ariaLabel="累计流入与流出面积图"
      footer={
        <>
          <span className="gew-chart__legend-item">
            <span
              className="gew-chart__legend-swatch"
              style={{ background: CHART_COLORS.inflow }}
              aria-hidden="true"
            />
            累计收入
          </span>
          <span className="gew-chart__legend-item">
            <span
              className="gew-chart__legend-swatch"
              style={{ background: CHART_COLORS.outflow }}
              aria-hidden="true"
            />
            累计支出
          </span>
          <span className="gew-chart__legend-item">
            <span
              className="gew-chart__legend-swatch"
              style={{ background: CHART_COLORS.series[1] }}
              aria-hidden="true"
            />
            累计净额
          </span>
        </>
      }
    />
  );
}
