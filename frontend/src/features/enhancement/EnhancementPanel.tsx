/**
 * 资金安排参考：结算延期压力、未来 7 天日常收付参考、建议经营留底。
 *
 * 设计原则（面向普通经营者，不面向算法工程师）：
 * * 默认层只讲业务含义，技术指标收进「查看计算依据」
 * * 历史参考（预测）明确声明「不计入今天可提用金额」
 * * 不可行状态下绝不出现「资金安排可行」这类结论
 * * 留底确认只在用户点击事件中执行，页面加载与数据刷新都不会自动提交
 */

import { useState } from 'react';
import { useMutation, useQuery } from '@tanstack/react-query';
import {
  Alert,
  App as AntdApp,
  Button,
  Col,
  Collapse,
  Empty,
  Row,
  Segmented,
  Skeleton,
  Space,
  Table,
  Tooltip,
} from 'antd';
import { InfoCircleOutlined, ReloadOutlined } from '@ant-design/icons';

import { enhancementApi, type EnhancementOverview } from '@/api/enhancements';
import { errorMessage } from '@/api/client';
import { queryKeys, queryClient } from '@/api/queryClient';
import { InlineNote, MetricCard, SectionCard, StatusTag } from '@/components/ui';
import { formatCny, formatSigned } from '@/utils/money';
import { STATUS_TONE, decisionCopy } from '@/utils/labels';
import type { AnalysisStatus } from '@/types';

const DELAY_OPTIONS = [0, 1, 2, 3, 4, 7];

/** 结算延期压力：让用户直接选「晚几天」，而不是看模型名。 */
function SettlementPressureBlock({ data }: { data: EnhancementOverview['settlement_pressure'] }) {
  if (!data.available) {
    return (
      <Empty
        image={Empty.PRESENTED_IMAGE_SIMPLE}
        description={data.message ?? '当前没有可分析的结算款'}
      />
    );
  }
  const onTime = data.scenarios.find((item) => item.id === 'on_time');
  const manual = data.scenarios.find((item) => item.id === 'manual_delay');
  const sample = data.scenarios.find((item) => item.id === 'sample_delay');

  return (
    <div className="gew-stack">
      <Row gutter={[16, 16]}>
        <Col xs={24} md={8}>
          <MetricCard
            label="按当前计划"
            value={formatCny(onTime?.max_withdrawable_cents ?? null)}
            tone={(onTime?.max_withdrawable_cents ?? 0) > 0 ? 'default' : 'default'}
            footnote="今日可提用"
          />
        </Col>
        <Col xs={24} md={8}>
          <MetricCard
            label="即使不提用仍缺"
            value={formatCny(manual?.payment_gap_cents ?? 0)}
            tone={(manual?.payment_gap_cents ?? 0) > 0 ? 'danger' : 'default'}
            footnote="付款缺口"
          />
        </Col>
        <Col xs={24} md={8}>
          <MetricCard
            label="距经营留底还差"
            value={formatCny(manual?.buffer_gap_cents ?? 0)}
            tone={(manual?.buffer_gap_cents ?? 0) > 0 ? 'danger' : 'default'}
            footnote="留底缺口"
          />
        </Col>
      </Row>

      <Table
        size="small"
        rowKey="id"
        pagination={false}
        dataSource={data.scenarios}
        columns={[
          {
            title: '情形',
            dataIndex: 'label',
            render: (value: string) => <span style={{ fontWeight: 500 }}>{value}</span>,
          },
          {
            title: '状态',
            dataIndex: 'status_label',
            width: 150,
            render: (value: string, row) => (
              <StatusTag tone={STATUS_TONE[row.status as AnalysisStatus]}>{value}</StatusTag>
            ),
          },
          {
            title: '今日可提用',
            dataIndex: 'max_withdrawable_cents',
            width: 140,
            align: 'right' as const,
            render: (value: number | null) => (
              <span className="num">{formatCny(value)}</span>
            ),
          },
        ]}
      />

      {manual && manual.status !== 'FEASIBLE' ? (
        <Alert
          type={manual.status === 'PAYMENT_GAP' ? 'error' : 'warning'}
          showIcon
          message={
            manual.status === 'PAYMENT_GAP'
              ? `如果结算再晚 ${manual.delay_days} 天，即使不提用家庭资金也仍缺 ${formatCny(
                  manual.payment_gap_cents,
                )}`
              : `如果结算再晚 ${manual.delay_days} 天，会低于经营留底，还差 ${formatCny(
                  manual.buffer_gap_cents,
                )}`
          }
          description="「可提用 0 元」只表示没有安全金额，并不代表资金安排已经可行。"
        />
      ) : null}

      {sample ? (
        <InlineNote tone="info">{sample.basis}</InlineNote>
      ) : null}

      {data.stats ? (
        <InlineNote tone={data.stats.has_sample ? 'info' : 'warning'}>
          {data.stats.headline}
          {data.stats.open_count > 0
            ? `；另有 ${data.stats.open_count} 笔未完成项，未参与统计。`
            : ''}
        </InlineNote>
      ) : null}
    </div>
  );
}

/** 未来 7 天日常收付参考。 */
function ForecastBlock({ data }: { data: EnhancementOverview['forecast'] }) {
  if (!data.available) {
    return (
      <Alert
        type="info"
        showIcon
        message="历史记录还不够"
        description={
          <>
            <div>{data.message}</div>
            <div style={{ marginTop: 8 }}>
              今日可提用金额、现金事件、情景分析、家庭协同与经营咨询都照常可用。
            </div>
          </>
        }
      />
    );
  }

  return (
    <div className="gew-stack">
      {data.needs_review ? (
        <Alert
          type="warning"
          showIcon
          message="近期经营变化较大，这部分历史参考需要人工复核。"
          description={data.needs_review_reason}
        />
      ) : null}

      <Table
        size="small"
        rowKey="day"
        pagination={false}
        scroll={{ x: 520 }}
        dataSource={data.daily}
        columns={[
          { title: '日期', dataIndex: 'day', width: 120 },
          {
            title: '日常预计到账',
            dataIndex: 'inflow_cents',
            align: 'right' as const,
            render: (value: number) => <span className="num">{formatCny(value)}</span>,
          },
          {
            title: '日常预计采购',
            dataIndex: 'outflow_cents',
            align: 'right' as const,
            render: (value: number) => <span className="num">{formatCny(value)}</span>,
          },
          {
            title: '净变化参考',
            dataIndex: 'net_cents',
            align: 'right' as const,
            render: (value: number) => (
              <span className="num">{formatSigned(value)}</span>
            ),
          },
        ]}
      />

      <InlineNote tone="neutral">{data.disclosure}</InlineNote>

      <Collapse
        ghost
        items={[
          {
            key: 'basis',
            label: '查看计算依据',
            children: (
              <div className="gew-stack">
                <div style={{ fontSize: 13, color: 'var(--text-secondary)' }}>
                  使用历史完整日 {data.history_days} 天，训练截止 {data.training_end}。
                  到账参考方法：{data.method_inflow_label}；采购参考方法：
                  {data.method_outflow_label}。
                </div>
                {Object.entries(data.diagnostics).map(([field, raw]) => {
                  const item = raw as Record<string, unknown>;
                  if (!item || typeof item !== 'object') return null;
                  return (
                    <div key={field} style={{ fontSize: 12, color: 'var(--text-muted)' }}>
                      {field === 'inflow' ? '到账' : '采购'}：训练段 MAE{' '}
                      {String(item.selected_validation_mae_cents)} 分，留出段 MAE{' '}
                      {String(item.selected_holdout_mae_cents)} 分，基线留出段 MAE{' '}
                      {String(item.baseline_holdout_mae_cents)} 分。
                    </div>
                  );
                })}
                <InlineNote tone="neutral">
                  这些是诊断指标，滚动窗口相互重叠，不是独立样本，也不代表未来准确率。
                </InlineNote>
              </div>
            ),
          },
        ]}
      />
    </div>
  );
}

/** 建议经营留底。 */
function ReserveBlock({
  data,
  overview,
  onConfirmed,
}: {
  data: EnhancementOverview['reserve_advice'];
  overview: EnhancementOverview;
  onConfirmed: () => void;
}) {
  const { message } = AntdApp.useApp();
  const [confirming, setConfirming] = useState(false);

  const confirmMutation = useMutation({
    mutationFn: () =>
      enhancementApi.confirmReserve({
        suggested_reserve_cents: data.suggested_reserve_cents,
        basis_hash: overview.basis_hash,
        ledger_revision: overview.ledger_revision,
        history_revision: overview.history_revision,
        // 回传生成这条建议的运行 id：服务端要用它读回原计算参数复算，
        // 否则非默认参数（如延后天数 3）下看到的建议永远无法确认。
        run_id: overview.run_id,
      }),
    onSuccess: (result) => {
      message.success(result.message);
      onConfirmed();
    },
    onError: (error) => {
      message.error(errorMessage(error));
      onConfirmed();
    },
    onSettled: () => setConfirming(false),
  });

  const basis = data.basis as Record<string, unknown>;
  const sourceRefs = (basis.source_refs as string[] | undefined) ?? [];

  return (
    <div className="gew-stack">
      <Row gutter={[16, 16]}>
        <Col xs={12} md={12}>
          <MetricCard
            label="当前经营留底"
            value={formatCny(data.current_reserve_cents)}
            footnote="你希望始终保留在经营账户中的金额"
          />
        </Col>
        <Col xs={12} md={12}>
          <MetricCard
            label="建议经营留底"
            value={formatCny(data.suggested_reserve_cents)}
            tone={data.suggests_increase ? 'danger' : 'default'}
            footnote={
              data.suggests_increase
                ? `建议增加 ${formatCny(data.extra_cents)}`
                : '无需调整'
            }
          />
        </Col>
      </Row>

      <InlineNote tone={data.has_suggestion ? 'info' : 'neutral'}>{data.headline}</InlineNote>

      <Collapse
        ghost
        items={[
          {
            key: 'why',
            label: '为什么建议这个金额？',
            children: (
              <div className="gew-stack" style={{ fontSize: 13 }}>
                <div>
                  使用了 {String(basis.block_count ?? data.block_count)} 个历史评估窗口（最少需要{' '}
                  {data.min_blocks} 个）。
                </div>
                <div>
                  历史最大累计不利误差：
                  {basis.empirical_error_text ? String(basis.empirical_error_text) : '暂无'}。
                </div>
                <div>参考分位数：q = {data.quantile}</div>
                <div>取整规则：向上取整到 {formatCny(data.rounding_cents)}</div>
                <div>数据截止：{overview.history.last_day ?? '—'}</div>
                <div>来源数量：{sourceRefs.length} 条</div>
                <InlineNote tone="neutral">{data.disclosure}</InlineNote>
              </div>
            ),
          },
        ]}
      />

      <Space wrap>
        <Tooltip
          title={
            data.suggests_increase
              ? '确认后会把经营留底更新为该金额，并重新计算所有相关结果'
              : '当前留底已经足够，无需调整'
          }
        >
          <Button
            type="primary"
            disabled={!data.suggests_increase}
            loading={confirming || confirmMutation.isPending}
            onClick={() => {
              // 只在这里提交：页面加载与数据刷新都不会自动应用
              setConfirming(true);
              confirmMutation.mutate();
            }}
          >
            确认采用 {formatCny(data.suggested_reserve_cents)}
          </Button>
        </Tooltip>
        <Button onClick={onConfirmed}>重新计算</Button>
      </Space>

      <InlineNote tone="neutral">
        建议不会自动生效，也不会自动降低你现有的经营留底。
      </InlineNote>
    </div>
  );
}

export default function EnhancementPanel() {
  const [delayDays, setDelayDays] = useState(2);

  const overviewQuery = useQuery({
    queryKey: queryKeys.enhancementOverview({ delay_days: delayDays }),
    queryFn: () => enhancementApi.overview({ delay_days: delayDays }),
  });

  const refreshAll = () => {
    queryClient.invalidateQueries({ queryKey: ['enhancements'] });
    queryClient.invalidateQueries({ queryKey: queryKeys.todayAnalysis });
    queryClient.invalidateQueries({ queryKey: queryKeys.accountOverview });
    queryClient.invalidateQueries({ queryKey: queryKeys.analysisStale });
    queryClient.invalidateQueries({ queryKey: queryKeys.dailyHistory });
  };

  if (overviewQuery.isLoading) {
    return (
      <SectionCard title="资金安排参考">
        <Skeleton active paragraph={{ rows: 3 }} />
      </SectionCard>
    );
  }

  if (overviewQuery.isError || !overviewQuery.data) {
    return (
      <SectionCard title="资金安排参考">
        <Alert
          type="error"
          showIcon
          message="暂时无法生成资金安排参考"
          description={errorMessage(overviewQuery.error)}
          action={
            <Button size="small" onClick={() => overviewQuery.refetch()}>
              重试
            </Button>
          }
        />
      </SectionCard>
    );
  }

  const data = overviewQuery.data;
  const copy = decisionCopy(data.baseline.status as AnalysisStatus);

  return (
    <div className="gew-stack">
      <SectionCard
        title="资金安排参考"
        extra={
          <Space>
            <Tooltip title="重新计算">
              <Button
                size="small"
                icon={<ReloadOutlined />}
                loading={overviewQuery.isFetching}
                onClick={() => overviewQuery.refetch()}
              />
            </Tooltip>
          </Space>
        }
      >
        <InlineNote tone="neutral">
          <InfoCircleOutlined aria-hidden="true" style={{ marginRight: 6 }} />
          下面三段都只是安排参考。历史经验参考不计入今天可提用金额——今天能拿多少
          始终由已确认的收付款事项决定。
        </InlineNote>

        <div style={{ marginTop: 12 }}>
          <InlineNote tone={copy.negative ? 'warning' : 'info'}>
            当前结论：{copy.headline}
            {data.baseline.max_withdrawable_cents !== null
              ? ` ${formatCny(data.baseline.max_withdrawable_cents)}`
              : ''}
            （状态：{data.baseline.status_label}）
          </InlineNote>
        </div>
      </SectionCard>

      <SectionCard
        title="如果结算晚到"
        extra={
          <Segmented
            size="small"
            value={delayDays}
            onChange={(value) => setDelayDays(Number(value))}
            options={DELAY_OPTIONS.map((value) => ({
              label: value === 0 ? '不延迟' : `${value} 天`,
              value,
            }))}
          />
        }
      >
        <SettlementPressureBlock data={data.settlement_pressure} />
      </SectionCard>

      <SectionCard title="接下来 7 天日常收付参考">
        <ForecastBlock data={data.forecast} />
      </SectionCard>

      <SectionCard title="建议经营留底">
        <ReserveBlock data={data.reserve_advice} overview={data} onConfirmed={refreshAll} />
      </SectionCard>
    </div>
  );
}

/** 供「查看依据」抽屉使用：来源引用列表。 */
export function SourceRefList({ refs }: { refs: string[] }) {
  if (!refs.length)
    return <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="没有来源记录" />;
  return (
    <div className="gew-kv-list">
      {refs.slice(0, 20).map((item) => (
        <div className="gew-kv-list__row" key={item}>
          <span className="gew-kv-list__key">{item}</span>
        </div>
      ))}
      {refs.length > 20 ? (
        <div className="gew-kv-list__row">
          <span className="gew-kv-list__key">另有 {refs.length - 20} 条来源未展示</span>
        </div>
      ) : null}
    </div>
  );
}
