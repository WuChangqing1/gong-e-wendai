/** 图表容器：负责初始化、响应式尺寸与空状态。 */

import { useEffect, useRef, type ReactNode } from 'react';
import type { EChartsOption } from 'echarts';

import echarts from '@/components/charts/echarts';

export interface ChartFrameProps {
  option: EChartsOption;
  height?: number;
  /** 无数据时展示的提示 */
  emptyHint?: string;
  isEmpty?: boolean;
  ariaLabel?: string;
  /** 图例或脚注 */
  footer?: ReactNode;
}

export default function ChartFrame({
  option,
  height = 260,
  isEmpty = false,
  emptyHint = '暂无数据',
  ariaLabel,
  footer,
}: ChartFrameProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef<echarts.ECharts | null>(null);

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

  if (isEmpty) {
    return (
      <div
        className="gew-chart-empty"
        style={{ height }}
        role="img"
        aria-label={ariaLabel ?? emptyHint}
      >
        <span>{emptyHint}</span>
      </div>
    );
  }

  return (
    <div>
      <div
        ref={containerRef}
        className="gew-chart"
        style={{ height }}
        role="img"
        aria-label={ariaLabel}
      />
      {footer ? <div className="gew-chart__legend">{footer}</div> : null}
    </div>
  );
}
