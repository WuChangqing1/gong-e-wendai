/**
 * 图表的懒加载入口。
 *
 * ECharts 相关代码约 542KB（gzip 182KB），而它只被「今日决策」与「情景分析」
 * 两个页面用到。此前被手动分包成静态 chunk 后，`index.html` 会对它做
 * modulepreload —— 结果是家庭协同、经营咨询、我的这些完全不含图表的页面
 * 也要下载这 542KB。
 *
 * 这里改为按需加载：只有真正要渲染图表时才拉取图表 chunk。
 * 图表组件都在「分析结果已就绪」的分支里渲染，因此不会产生多余请求。
 */

import { lazy } from 'react';

/** 未来 7 天资金趋势（阶梯线）。 */
export const CashflowChartLazy = lazy(() => import('@/components/CashflowChart'));

/** 图表化分析面板：每日收支、收支结构、余额变化、积累节奏、到账分布、情景对比。 */
export const AnalysisChartsPanelLazy = lazy(
  () => import('@/features/analysis/AnalysisChartsPanel'),
);

/**
 * 图表加载中的占位。
 *
 * 必须预先占用与真实图表接近的高度，否则图表 chunk 到达时会把下方内容
 * 突然推下去（移动端尤其明显）。
 */
export function ChartLoading({ height = 260 }: { height?: number }) {
  return (
    <div
      className="gew-chart-loading"
      style={{ height }}
      role="status"
      aria-live="polite"
      aria-label="图表加载中"
    >
      <span className="gew-chart-loading__text">图表加载中…</span>
    </div>
  );
}
