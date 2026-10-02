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
  axisPointer: { type: 'shadow' as const },
  backgroundColor: 'rgba(255,255,255,0.98)',
  borderColor: '#E8E8E8',
  borderWidth: 1,
  textStyle: { color: '#1F1F1F', fontSize: 12 },
  extraCssText: 'box-shadow: 0 2px 8px rgba(15,23,42,0.08); border-radius: 8px;',
};

export default echarts;
