/**
 * 未来 7 天分析面板（图表为主，文字为辅）。
 *
 * 数据来自 `/analysis/window-summary`，全部由确定性引擎的事件扫描结果聚合：
 * 每日收支、按类型的收支结构、待结算到账分布。
 *
 * 情景分析页与今日决策页共用本组件，保证两处口径一致。
 */

import { useMemo } from 'react';
import { Alert, Skeleton } from 'antd';
import { useQuery } from '@tanstack/react-query';

import { analysisApi } from '@/api/cashflow';
import { errorMessage } from '@/api/client';
import { queryKeys } from '@/api/queryClient';
import ArrivalTimelineChart from '@/components/charts/ArrivalTimelineChart';
import BalanceWaterfallChart from '@/components/charts/BalanceWaterfallChart';
import CategoryPieChart from '@/components/charts/CategoryPieChart';
import CumulativeFlowChart from '@/components/charts/CumulativeFlowChart';
import DailyFlowChart from '@/components/charts/DailyFlowChart';
import ScenarioComparisonChart from '@/components/charts/ScenarioComparisonChart';
import { SectionCard } from '@/components/ui';
import type { AnalysisResult, WindowSummary } from '@/types';
import { formatCny, formatSigned } from '@/utils/money';

function MiniMetric({
  label,
  value,
  tone = 'default',
}: {
  label: string;
  value: string;
  tone?: 'default' | 'inflow' | 'danger';
}) {
  const modifier =
    tone === 'inflow'
      ? ' gew-mini-metric__value--inflow'
      : tone === 'danger'
        ? ' gew-mini-metric__value--danger'
        : '';
  return (
    <div className="gew-mini-metric">
      <span className="gew-mini-metric__label">{label}</span>
      <span className={`gew-mini-metric__value${modifier}`}>{value}</span>
    </div>
  );
}

export function AnalysisMetrics({ summary }: { summary: WindowSummary }) {
  const peakDay = useMemo(
    () =>
      summary.daily_terms.reduce(
        (best, item) => (item.outflow_cents > (best?.outflow_cents ?? -1) ? item : best),
        summary.daily_terms[0],
      ),
    [summary.daily_terms],
  );
  const lowestDay = useMemo(
    () =>
      summary.daily_terms.reduce(
        (best, item) =>
          item.closing_balance_cents < (best?.closing_balance_cents ?? Number.MAX_SAFE_INTEGER)
            ? item
            : best,
        summary.daily_terms[0],
      ),
    [summary.daily_terms],
  );

  return (
    <div className="gew-mini-metrics">
      <MiniMetric label="期初余额" value={formatCny(summary.opening_balance_cents)} />
      <MiniMetric
        label="计划收入"
        value={formatCny(summary.scheduled_inflow_cents)}
        tone="inflow"
      />
      <MiniMetric label="计划支出" value={formatCny(summary.scheduled_outflow_cents)} />
      <MiniMetric
        label="净变化"
        value={formatSigned(summary.net_change_cents)}
        tone={summary.net_change_cents < 0 ? 'danger' : 'inflow'}
      />
      <MiniMetric label="期末余额" value={formatCny(summary.closing_balance_cents)} />
      <MiniMetric
        label="最低余额日"
        value={lowestDay ? `${lowestDay.day} · ${formatCny(lowestDay.closing_balance_cents)}` : '--'}
      />
      <MiniMetric
        label="支出最多的一天"
        value={peakDay ? `${peakDay.day} · ${formatCny(peakDay.outflow_cents)}` : '--'}
      />
      <MiniMetric label="事项笔数" value={`${summary.event_count} 笔`} />
    </div>
  );
}

export default function AnalysisChartsPanel({
  analysis,
  compact = false,
}: {
  analysis: AnalysisResult | null;
  /** 今日决策页使用紧凑模式：默认只渲染核心图表 */
  compact?: boolean;
}) {
  const summaryQuery = useQuery({
    queryKey: queryKeys.windowSummary(),
    queryFn: () => analysisApi.windowSummary(),
  });

  const summary = summaryQuery.data;
  const points = analysis?.points ?? [];
  const scenarios = analysis?.scenarios ?? [];

  if (summaryQuery.isLoading) {
    return (
      <SectionCard title="本期图表">
        <Skeleton active paragraph={{ rows: 5 }} />
      </SectionCard>
    );
  }

  if (summaryQuery.isError || !summary) {
    return (
      <SectionCard title="本期图表">
        <Alert
          type="warning"
          showIcon
          message="图表数据暂时不可用"
          description={errorMessage(summaryQuery.error)}
          action={
            <button type="button" className="ant-btn" onClick={() => summaryQuery.refetch()}>
              重试
            </button>
          }
        />
      </SectionCard>
    );
  }

  const dailyChart = (
    <SectionCard title="每日收入与支出">
      <DailyFlowChart terms={summary.daily_terms} />
    </SectionCard>
  );

  const waterfallChart = (
    <SectionCard
      title="余额变化过程"
      extra={
        <span style={{ fontSize: 12, color: 'var(--text-muted)' }}>从期初逐笔到期末</span>
      }
    >
      <BalanceWaterfallChart points={points} />
    </SectionCard>
  );

  const categoryChart = (
    <SectionCard title="收支结构">
      <CategoryPieChart terms={summary.category_terms} />
    </SectionCard>
  );

  const cumulativeChart = (
    <SectionCard title="资金积累节奏">
      <CumulativeFlowChart terms={summary.daily_terms} />
    </SectionCard>
  );

  const arrivalChart = (
    <SectionCard title="待结算资金到账分布">
      <ArrivalTimelineChart arrivals={summary.arrival_terms} dailyTerms={summary.daily_terms} />
    </SectionCard>
  );

  const scenarioChart = (
    <SectionCard
      title="情景关键指标对比"
      extra={
        <span style={{ fontSize: 12, color: 'var(--text-muted)' }}>
          可提用 / 最紧时点 / 期末余额
        </span>
      }
    >
      <ScenarioComparisonChart scenarios={scenarios} />
    </SectionCard>
  );

  if (compact) {
    return (
      <div className="gew-stack">
        <SectionCard title="本期资金概览" bodyClassName="gew-card__body--tight">
          <AnalysisMetrics summary={summary} />
        </SectionCard>
        <div className="gew-chart-grid">
          {dailyChart}
          {arrivalChart}
        </div>
      </div>
    );
  }

  return (
    <div className="gew-stack">
      <SectionCard title="本期资金概览" bodyClassName="gew-card__body--tight">
        <AnalysisMetrics summary={summary} />
      </SectionCard>

      <div className="gew-chart-grid">{dailyChart}{categoryChart}</div>
      <div className="gew-chart-grid gew-chart-grid--wide">{waterfallChart}</div>
      <div className="gew-chart-grid">{cumulativeChart}{arrivalChart}</div>
      {scenarios.length > 0 ? (
        <div className="gew-chart-grid gew-chart-grid--wide">{scenarioChart}</div>
      ) : null}
    </div>
  );
}
