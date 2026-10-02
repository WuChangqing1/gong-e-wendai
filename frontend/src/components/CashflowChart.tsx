/**
 * 未来 7 天资金趋势图（ECharts 阶梯线）。
 *
 * 设计要点：
 * * 阶梯线（step line）表达"余额在事件发生的瞬间跳变"
 * * 同时标出 0 元线、经营留底线、风险时点、关键事件
 * * 风险不只用颜色表达——用 markPoint 图标 + 文字标注 + 数值共同表达
 * * 支持多情景叠加（按当前计划 / 到账延迟）
 */

import { useEffect, useMemo, useRef } from 'react';
import * as echarts from 'echarts/core';
import { LineChart } from 'echarts/charts';
import {
  GridComponent,
  MarkLineComponent,
  MarkPointComponent,
  TitleComponent,
  TooltipComponent,
} from 'echarts/components';
import { CanvasRenderer } from 'echarts/renderers';
import type { EChartsOption } from 'echarts';

import type { BalancePoint, ScenarioCurve } from '@/types';
import { formatCny } from '@/utils/money';
import { formatDateTime } from '@/utils/datetime';

echarts.use([
  LineChart,
  GridComponent,
  TooltipComponent,
  MarkLineComponent,
  MarkPointComponent,
  TitleComponent,
  CanvasRenderer,
]);

const SERIES_COLORS = ['#D90000', '#3568A8', '#178A4B'];

export interface CashflowChartProps {
  scenarios: ScenarioCurve[];
  bufferCents: number;
  height?: number;
  /** 是否显示图例说明 */
  showLegend?: boolean;
}

interface SeriesPoint {
  value: [number, number];
  name: string;
  direction: string | null;
  deltaText: string;
  balanceText: string;
}

function toSeries(points: BalancePoint[]): SeriesPoint[] {
  return points.map((point) => ({
    value: [new Date(point.timestamp).getTime(), point.balance_cents / 100] as [number, number],
    name: point.event_title,
    direction: point.direction,
    deltaText: point.is_opening ? '期初余额' : point.delta_text,
    balanceText: point.balance_text,
  }));
}

export default function CashflowChart({
  scenarios,
  bufferCents,
  height = 320,
  showLegend = true,
}: CashflowChartProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef<echarts.ECharts | null>(null);

  const option = useMemo<EChartsOption>(() => {
    const primary = scenarios[0];
    const primaryPoints = primary?.points ?? [];
    const series = scenarios.map((scenario, index) => {
      const data = toSeries(scenario.points);
      const color = SERIES_COLORS[index % SERIES_COLORS.length];
      const limitingTs = scenario.limiting_timestamp
        ? new Date(scenario.limiting_timestamp).getTime()
        : null;
      const markPoints =
        limitingTs !== null && scenario.limiting_balance_cents !== null
          ? [
              {
                name: scenario.limiting_balance_cents < 0 ? '付款缺口' : '最紧张时点',
                coord: [limitingTs, scenario.limiting_balance_cents / 100] as [number, number],
                value: formatCny(scenario.limiting_balance_cents),
                itemStyle: {
                  color: scenario.limiting_balance_cents < 0 ? '#C62828' : '#D97706',
                },
                label: {
                  formatter: scenario.limiting_balance_cents < 0 ? '付款缺口' : '最紧张时点',
                  color: '#fff',
                  fontSize: 11,
                },
              },
            ]
          : [];

      return {
        name: scenario.label,
        type: 'line' as const,
        step: 'end' as const,
        symbol: 'circle',
        symbolSize: 7,
        showSymbol: true,
        lineStyle: { width: index === 0 ? 2.4 : 1.8, color },
        itemStyle: { color },
        emphasis: { focus: 'series' as const },
        data,
        markPoint: { data: markPoints, symbolSize: 44 },
        markLine:
          index === 0
            ? {
                silent: true,
                symbol: 'none',
                label: {
                  position: 'insideEndTop' as const,
                  formatter: (params: { name?: string }) => params.name ?? '',
                  fontSize: 11,
                },
                data: [
                  {
                    yAxis: 0,
                    name: '0 元线',
                    lineStyle: { color: '#C62828', type: 'solid' as const, width: 1 },
                    label: { color: '#C62828' },
                  },
                  {
                    yAxis: bufferCents / 100,
                    name: `经营留底 ${formatCny(bufferCents)}`,
                    lineStyle: { color: '#D97706', type: 'dashed' as const, width: 1 },
                    label: { color: '#D97706' },
                  },
                ],
              }
            : undefined,
      };
    });

    const allValues = primaryPoints.map((point) => point.balance_cents / 100);
    const bufferValue = bufferCents / 100;
    const values = [...allValues, bufferValue, 0];
    const min = Math.min(...values);
    const max = Math.max(...values);
    const pad = Math.max(100, (max - min) * 0.18);

    return {
      grid: { left: 8, right: 24, top: 24, bottom: 8, containLabel: true },
      tooltip: {
        trigger: 'axis',
        axisPointer: { type: 'line', lineStyle: { color: '#BFBFBF', type: 'dashed' } },
        formatter: (params: unknown) => {
          const list = Array.isArray(params) ? params : [params];
          const first = list[0] as { data?: SeriesPoint; axisValue?: number } | undefined;
          const when = first?.axisValue ? formatDateTime(new Date(first.axisValue)) : '';
          const lines = list.map((item) => {
            const raw = item as { seriesName?: string; data?: SeriesPoint; color?: string };
            const point = raw.data;
            const balance = point?.balanceText ?? '--';
            const delta = point?.deltaText ?? '';
            const eventName = point?.name ?? '';
            const marker = `<span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:${raw.color};margin-right:6px"></span>`;
            return `${marker}${raw.seriesName ?? ''}：<b>${balance}</b>${
              eventName ? ` · ${eventName}${delta ? `（${delta}）` : ''}` : ''
            }`;
          });
          return [`<div style="font-size:12px;color:#666;margin-bottom:4px">${when}</div>`, ...lines].join(
            '<br/>',
          );
        },
      },
      xAxis: {
        type: 'time',
        boundaryGap: false,
        axisLine: { lineStyle: { color: '#E8E8E8' } },
        axisLabel: {
          color: '#999',
          fontSize: 11,
          formatter: (value: number) => formatDateTime(new Date(value)).slice(5, 16),
        },
        splitLine: { show: false },
      },
      yAxis: {
        type: 'value',
        min: Math.floor(min - pad),
        max: Math.ceil(max + pad),
        axisLabel: {
          color: '#999',
          fontSize: 11,
          formatter: (value: number) => `${value.toLocaleString('zh-CN')}`,
        },
        splitLine: { lineStyle: { color: '#F0F0F0' } },
      },
      series,
    };
  }, [scenarios, bufferCents]);

  useEffect(() => {
    if (!containerRef.current) return undefined;
    const chart = echarts.init(containerRef.current, undefined, { renderer: 'canvas' });
    chartRef.current = chart;
    const handleResize = () => chart.resize();
    window.addEventListener('resize', handleResize);
    return () => {
      window.removeEventListener('resize', handleResize);
      chart.dispose();
      chartRef.current = null;
    };
  }, []);

  useEffect(() => {
    chartRef.current?.setOption(option, true);
  }, [option]);

  return (
    <div>
      <div ref={containerRef} className="gew-chart" style={{ height }} role="img" aria-label="未来 7 天资金趋势图" />
      {showLegend ? (
        <div className="gew-chart__legend">
          {scenarios.map((scenario, index) => (
            <span className="gew-chart__legend-item" key={scenario.label}>
              <span
                className="gew-chart__legend-swatch"
                style={{ background: SERIES_COLORS[index % SERIES_COLORS.length] }}
                aria-hidden="true"
              />
              {scenario.label}
              <span style={{ color: 'var(--text-muted)' }}>
                可提用 {formatCny(scenario.max_withdrawable_cents)}
              </span>
            </span>
          ))}
          <span className="gew-chart__legend-item">
            <span
              className="gew-chart__legend-swatch"
              style={{ background: '#C62828' }}
              aria-hidden="true"
            />
            0 元线（付款缺口）
          </span>
          <span className="gew-chart__legend-item">
            <span
              className="gew-chart__legend-swatch"
              style={{ background: '#D97706' }}
              aria-hidden="true"
            />
            经营留底 {formatCny(bufferCents)}
          </span>
        </div>
      ) : null}
    </div>
  );
}
