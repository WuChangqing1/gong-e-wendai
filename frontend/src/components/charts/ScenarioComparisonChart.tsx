/**
 * 情景对比分组柱状图。
 *
 * 每个情景三个指标并排：今日可提用、最紧时点余额、期末余额，
 * 并用一条 0 元参考线标出缺口位置。
 */

import { useMemo } from 'react';
import type { EChartsOption } from 'echarts';

import ChartFrame from '@/components/charts/ChartFrame';
import { CHART_COLORS, TOOLTIP_STYLE, yuanAxisLabel } from '@/components/charts/echarts';
import type { ScenarioCurve } from '@/types';
import { formatCny } from '@/utils/money';

export default function ScenarioComparisonChart({
  scenarios,
  height = 280,
}: {
  scenarios: ScenarioCurve[];
  height?: number;
}) {
  const option = useMemo<EChartsOption>(() => {
    const labels = scenarios.map((item) => item.label);

    return {
      grid: { left: 8, right: 16, top: 32, bottom: 8, containLabel: true },
      tooltip: {
        ...TOOLTIP_STYLE,
        formatter: (params: unknown) => {
          const list = Array.isArray(params) ? params : [params];
          const index = (list[0] as { dataIndex?: number }).dataIndex ?? 0;
          const scenario = scenarios[index];
          if (!scenario) return '';
          return [
            `<b>${scenario.label}</b>`,
            `今日可提用 <b>${formatCny(scenario.max_withdrawable_cents)}</b>`,
            `最紧时点余额 <b>${formatCny(scenario.limiting_balance_cents)}</b>`,
            `期末余额 <b>${formatCny(scenario.end_balance_cents)}</b>`,
            `付款缺口 ${formatCny(scenario.payment_gap_cents)} · 留底缺口 ${formatCny(scenario.buffer_gap_cents)}`,
          ].join('<br/>');
        },
      },
      legend: {
        top: 0,
        right: 0,
        itemWidth: 10,
        itemHeight: 10,
        textStyle: { color: '#666', fontSize: 12 },
        data: ['今日可提用', '最紧时点余额', '期末余额'],
      },
      xAxis: {
        type: 'category',
        data: labels,
        axisTick: { show: false },
        axisLine: { lineStyle: { color: '#E8E8E8' } },
        axisLabel: { color: '#666', fontSize: 12 },
      },
      yAxis: {
        type: 'value',
        axisLabel: { color: '#999', fontSize: 11, formatter: yuanAxisLabel },
        splitLine: { lineStyle: { color: '#F0F0F0' } },
      },
      series: [
        {
          name: '今日可提用',
          type: 'bar',
          barMaxWidth: 30,
          itemStyle: { color: CHART_COLORS.series[0] },
          data: scenarios.map((item) => (item.max_withdrawable_cents ?? 0) / 100),
        },
        {
          name: '最紧时点余额',
          type: 'bar',
          barMaxWidth: 30,
          itemStyle: { color: CHART_COLORS.series[3] },
          data: scenarios.map((item) => (item.limiting_balance_cents ?? 0) / 100),
          markLine: {
            silent: true,
            symbol: 'none',
            label: { formatter: '0 元线', color: CHART_COLORS.zero, fontSize: 11 },
            lineStyle: { color: CHART_COLORS.zero, type: 'dashed', width: 1 },
            data: [{ yAxis: 0 }],
          },
        },
        {
          name: '期末余额',
          type: 'bar',
          barMaxWidth: 30,
          itemStyle: { color: CHART_COLORS.series[2] },
          data: scenarios.map((item) => (item.end_balance_cents ?? 0) / 100),
        },
      ],
    };
  }, [scenarios]);

  return (
    <ChartFrame
      option={option}
      height={height}
      isEmpty={scenarios.length === 0}
      emptyHint="暂无可比较的情景"
      ariaLabel="多情景关键指标对比柱状图"
      footer={
        <>
          <span className="gew-chart__legend-item">
            <span
              className="gew-chart__legend-swatch"
              style={{ background: CHART_COLORS.series[0] }}
              aria-hidden="true"
            />
            今日可提用
          </span>
          <span className="gew-chart__legend-item">
            <span
              className="gew-chart__legend-swatch"
              style={{ background: CHART_COLORS.series[3] }}
              aria-hidden="true"
            />
            最紧时点余额
          </span>
          <span className="gew-chart__legend-item">
            <span
              className="gew-chart__legend-swatch"
              style={{ background: CHART_COLORS.series[2] }}
              aria-hidden="true"
            />
            期末余额
          </span>
          <span className="gew-chart__legend-item" style={{ color: 'var(--text-muted)' }}>
            柱高低于 0 元线即为缺口
          </span>
        </>
      }
    />
  );
}
