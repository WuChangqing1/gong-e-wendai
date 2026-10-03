/**
 * ECharts 按需注册。
 *
 * 所有图表组件共用这一份注册，避免每个组件各自 use() 导致重复打包。
 */

import * as echarts from 'echarts/core';
import { BarChart, LineChart, PieChart } from 'echarts/charts';
import {
  GridComponent,
  LegendComponent,
  MarkLineComponent,
  MarkPointComponent,
  TitleComponent,
  TooltipComponent,
} from 'echarts/components';
import { CanvasRenderer } from 'echarts/renderers';

echarts.use([
  BarChart,
  LineChart,
  PieChart,
  GridComponent,
  LegendComponent,
  MarkLineComponent,
  MarkPointComponent,
  TitleComponent,
  TooltipComponent,
  CanvasRenderer,
]);

/** 图表统一配色：品牌红为主，其余用于区分系列。 */
export const CHART_COLORS = {
  inflow: '#178A4B',
  outflow: '#D90000',
  balance: '#D90000',
  buffer: '#D97706',
  zero: '#C62828',
  series: ['#D90000', '#3568A8', '#178A4B', '#D97706', '#7A5AF8', '#0E7490'],
  soft: ['#F4CACA', '#D5E2F1', '#CFE9DB', '#F2DDB8', '#DED7FB', '#C7E3E9'],
} as const;

/** 金额轴标签：整数元，带千分位。 */
export function yuanAxisLabel(value: number): string {
  if (Math.abs(value) >= 10000) return `${(value / 10000).toFixed(1)}万`;
  return value.toLocaleString('zh-CN');
}

/** 图表容器通用 tooltip 样式。 */
export const TOOLTIP_STYLE = {
  trigger: 'axis' as const,
  // 让触摸设备可用：`mousemove` 之外同时监听 tap，
  // 手机没有 hover，重要信息不能只能靠悬停看到。
  triggerOn: 'mousemove|click' as const,
  axisPointer: { type: 'shadow' as const },
  backgroundColor: 'rgba(255,255,255,0.98)',
  borderColor: '#E8E8E8',
  borderWidth: 1,
  textStyle: { color: '#1F1F1F', fontSize: 12 },
  extraCssText: 'box-shadow: 0 2px 8px rgba(15,23,42,0.08); border-radius: 8px;',
};

/**
 * 手机端 X 轴标签精简参数。
 *
 * 窄屏（约 316px 画布）放不下「10月3日 / 周六」两行标签乘以 8-9 个刻度，
 * 因此手机端只显示日期（`10/3`），并把标签字号提到 12px。
 */
export const MOBILE_AXIS_LABEL = {
  fontSize: 12,
  interval: 'auto' as const,
  hideOverlap: true,
};

/** 手机端图例：移到图表下方一行，避免与标题、坐标轴挤在顶部。 */
export const MOBILE_LEGEND = {
  bottom: 0,
  left: 'center' as const,
  itemWidth: 10,
  itemHeight: 10,
  itemGap: 12,
  textStyle: { color: '#666', fontSize: 12 },
};

/** 手机端网格：给底部图例留出空间。 */
export const MOBILE_GRID = { left: 8, right: 12, top: 24, bottom: 44, containLabel: true };

export default echarts;
