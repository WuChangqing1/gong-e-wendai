/** 今日决策页：最大可提用金额是页面视觉中心。 */

import { Suspense, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useMutation, useQuery } from '@tanstack/react-query';
import {
  Alert,
  App as AntdApp,
  Button,
  Col,
  Drawer,
  Form,
  InputNumber,
  Modal,
  Row,
  Segmented,
  Skeleton,
  Space,
  Tooltip,
} from 'antd';
import {
  ArrowRightOutlined,
  BarChartOutlined,
  BulbOutlined,
  ReloadOutlined,
  ShareAltOutlined,
  WarningOutlined,
} from '@ant-design/icons';

import { merchantApi } from '@/api/auth';
import { analysisApi, cashEventApi, type AnalysisRunPayload } from '@/api/cashflow';
import { errorMessage } from '@/api/client';
import { queryKeys, queryClient } from '@/api/queryClient';
import {
  AnalysisChartsPanelLazy,
  CashflowChartLazy,
  ChartLoading,
} from '@/components/charts/lazy';
import {
  DescriptionGrid,
  InlineNote,
  MetricCard,
  PageHeader,
  SectionCard,
  StatusTag,
} from '@/components/ui';
import AiExplainPanel from '@/features/ai/AiExplainPanel';
import EnhancementPanel from '@/features/enhancement/EnhancementPanel';
import ShareCardDrawer from '@/features/household/ShareCardDrawer';
import { useIsMobile } from '@/hooks/useResponsive';
import type { AnalysisMode, AnalysisResult } from '@/types';
import { formatCny, splitCny } from '@/utils/money';
import { formatDateTime } from '@/utils/datetime';
import {
  FEASIBLE_ZERO_DETAIL,
  STATUS_TONE,
  decisionAmountCents,
  decisionCopy,
} from '@/utils/labels';

const MODE_OPTIONS: { label: string; value: AnalysisMode }[] = [
  { label: '按当前计划', value: 'current_plan' },
  { label: '到账延迟', value: 'delayed' },
  { label: '共同约束', value: 'joint' },
];

/** 「今日决策」页默认的到账延迟天数（与后端 window-summary 默认值一致）。 */
const DEFAULT_DELAY_DAYS = 2;

interface HeroProps {
  result: AnalysisResult;
  onOpenReason: () => void;
  onOpenShare: () => void;
  onGoToEvents: () => void;
}

function HeroAmount({ result, onOpenReason, onOpenShare, onGoToEvents }: HeroProps) {
  const copy = decisionCopy(result.status);
  const amountCents = decisionAmountCents(result);

  const parts = splitCny(amountCents ?? 0);
  const isZero = amountCents === 0;
  const modifier = copy.negative
    ? ' gew-hero__amount--gap'
    : isZero
      ? ' gew-hero__amount--zero'
      : '';

  const detail = (() => {
    if (result.status === 'INPUT_INCOMPLETE') {
      return copy.detail;
    }
    if (result.status === 'FEASIBLE') {
      return result.max_withdrawable_cents === 0 ? FEASIBLE_ZERO_DETAIL : copy.detail;
    }
    return result.limiting_reason || copy.detail;
  })();

  return (
    <section className="gew-hero">
      <div className="gew-hero__label">
        <span>{copy.headline}</span>
        <StatusTag tone={STATUS_TONE[result.status] as 'ok' | 'warning' | 'danger' | 'info'}>
          {result.status_label}
        </StatusTag>
        <span style={{ color: 'var(--text-muted)', fontSize: 12 }}>
          {result.mode === 'joint' ? '共同约束口径' : result.mode_label}
        </span>
      </div>

      <div className={`gew-hero__amount${modifier}`} aria-live="polite">
        {amountCents === null ? (
          <span className="gew-hero__integer">待确认</span>
        ) : (
          <>
            <span className="gew-hero__symbol">{parts.symbol}</span>
            <span className="gew-hero__integer">{parts.integer}</span>
            <span className="gew-hero__fraction">.{parts.fraction}</span>
          </>
        )}
      </div>

      {copy.negative ? (
        <p className="gew-hero__hint gew-hero__hint--strong">
          <WarningOutlined aria-hidden="true" style={{ marginRight: 6 }} />
          暂不建议提用家庭资金。
        </p>
      ) : null}

      <p className="gew-hero__hint">{detail}</p>

      {result.mode === 'joint' && result.binding_label ? (
        <p className="gew-hero__hint" style={{ marginTop: 8 }}>
          <WarningOutlined aria-hidden="true" style={{ color: 'var(--warning)', marginRight: 6 }} />
          最保守的结果来自「{result.binding_label}」情景。
        </p>
      ) : null}

      <div className="gew-hero__actions">
        <Button type="primary" icon={<BulbOutlined />} onClick={onOpenReason}>
          查看为什么
        </Button>
        <Button icon={<ShareAltOutlined />} onClick={onOpenShare}>
          分享给家庭
        </Button>
        {result.status === 'INPUT_INCOMPLETE' ? (
          <Button onClick={onGoToEvents}>去补充资料</Button>
        ) : null}
      </div>
    </section>
  );
}

export default function TodayPage() {
  const navigate = useNavigate();
  const isMobile = useIsMobile();
  const { message } = AntdApp.useApp();
  const [mode, setMode] = useState<AnalysisMode>('current_plan');
  // 到账延迟天数与本页口径同源：Hero、顶部图表、资金规划、延期压力全部用它，
  // 避免出现「页头按延迟 2 天、下面按延迟 3 天」这种自相矛盾。
  const [delayDays, setDelayDays] = useState(DEFAULT_DELAY_DAYS);
  const [reasonOpen, setReasonOpen] = useState(false);
  const [shareOpen, setShareOpen] = useState(false);
  const [snapshotOpen, setSnapshotOpen] = useState(false);
  const [form] = Form.useForm();

  const overviewQuery = useQuery({
    queryKey: queryKeys.accountOverview,
    queryFn: merchantApi.overview,
  });

  const staleQuery = useQuery({
    queryKey: queryKeys.analysisStale,
    queryFn: analysisApi.stale,
  });

  const analysisQuery = useQuery({
    queryKey: [...queryKeys.todayAnalysis, mode, delayDays],
    queryFn: () =>
      analysisApi.run({ mode, delay_days: delayDays } satisfies AnalysisRunPayload),
  });

  const limitEventQuery = useQuery({
    queryKey: queryKeys.cashEventDetail(analysisQuery.data?.limiting_event_id ?? 'none'),
    queryFn: () => cashEventApi.detail(analysisQuery.data!.limiting_event_id!),
    enabled: Boolean(analysisQuery.data?.limiting_event_id),
  });

  const snapshotMutation = useMutation({
    mutationFn: (values: { opening_balance_cents: number; buffer_cents: number }) =>
      merchantApi
        .createSnapshot({ opening_balance_cents: values.opening_balance_cents })
        .then(() => merchantApi.updateProfile({ default_buffer_amount_cents: values.buffer_cents })),
    onSuccess: () => {
      message.success('经营资金已登记');
      setSnapshotOpen(false);
      form.resetFields();
      queryClient.invalidateQueries({ queryKey: queryKeys.accountOverview });
      queryClient.invalidateQueries({ queryKey: [...queryKeys.todayAnalysis, mode] });
      queryClient.invalidateQueries({ queryKey: queryKeys.analysisStale });
    },
    onError: (error) => message.error(errorMessage(error)),
  });

  const result = analysisQuery.data;
  const overview = overviewQuery.data;
  const scenarios = result?.scenarios?.length ? result.scenarios : [];

  /**
   * 资金曲线与图表组。
   *
   * 桌面端紧随「当前资金」概览，手机端移到「最紧张时点」之后 ——
   * 手机上首屏要先给出结论与关键数字，图表属于第二层信息。
   */
  const moneyCharts = result ? (
    <>
      <SectionCard title="未来 7 天资金趋势">
        {/* 图表 chunk 按需加载；Suspense 只包住图表本身，不阻塞页面其余部分 */}
        <Suspense fallback={<ChartLoading height={320} />}>
          <CashflowChartLazy scenarios={scenarios} bufferCents={result.buffer_cents} />
        </Suspense>
      </SectionCard>

      {/* 图表化分析：每日收支与待结算到账分布 */}
      <Suspense fallback={<ChartLoading height={520} />}>
        <AnalysisChartsPanelLazy analysis={result} mode={mode} delayDays={delayDays} compact />
      </Suspense>
    </>
  ) : null;

  return (
    <div className="gew-stack">
      <PageHeader
        title="今日决策"
        extra={
          <Space wrap>
            <Segmented
              options={MODE_OPTIONS}
              value={mode}
              onChange={(value) => setMode(value as AnalysisMode)}
            />
            {isMobile ? (
              // 手机底部标签栏最多 5 项，情景分析不占位；入口放在首页，地址与桌面一致。
              <Button icon={<BarChartOutlined />} onClick={() => navigate('/analysis')}>
                情景分析
              </Button>
            ) : null}
            <Tooltip title="重新计算">
              <Button
                icon={<ReloadOutlined />}
                loading={analysisQuery.isFetching}
                onClick={() => {
                  analysisQuery.refetch();
                  staleQuery.refetch();
                  overviewQuery.refetch();
                }}
              />
            </Tooltip>
          </Space>
        }
      />

      {staleQuery.data?.is_stale ? (
        <Alert
          type="warning"
          showIcon
          message="资金数据已更新，请查看最新结果"
          description={`${staleQuery.data.stale_reason ?? '收付款事项或资金时点已变更'}。下方结果已按最新数据重新计算。`}
          action={
            <Button
              size="small"
              onClick={() => {
                analysisQuery.refetch();
                staleQuery.refetch();
              }}
            >
              刷新结果
            </Button>
          }
        />
      ) : null}

      {overview && !overview.has_snapshot ? (
        <Alert
          type="info"
          showIcon
          message="先登记当前经营资金"
          description="登记经营账户当前可用的已结算余额，以及你希望保留的经营留底，系统才能计算今天可以提用的金额。"
          action={
            <Button size="small" type="primary" onClick={() => setSnapshotOpen(true)}>
              去登记
            </Button>
          }
        />
      ) : null}

      {analysisQuery.isLoading ? (
        <SectionCard>
          <Skeleton active paragraph={{ rows: 3 }} />
        </SectionCard>
      ) : analysisQuery.isError ? (
        <Alert
          type="error"
          showIcon
          message="分析失败"
          description={errorMessage(analysisQuery.error)}
          action={<Button size="small" onClick={() => analysisQuery.refetch()}>重试</Button>}
        />
      ) : result ? (
        <>
          <HeroAmount
            result={result}
            onOpenReason={() => setReasonOpen(true)}
            onOpenShare={() => setShareOpen(true)}
            onGoToEvents={() => navigate('/events')}
          />

          <Row gutter={[16, 16]}>
            <Col xs={12} lg={6}>
              <SectionCard flat bodyClassName="gew-card__body--tight">
                <MetricCard
                  label="当前可用"
                  value={formatCny(result.opening_balance_cents)}
                  footnote={overview?.snapshot_at ? `时点 ${formatDateTime(overview.snapshot_at)}` : undefined}
                />
              </SectionCard>
            </Col>
            <Col xs={12} lg={6}>
              <SectionCard flat bodyClassName="gew-card__body--tight">
                <MetricCard
                  label={
                    <Tooltip title="尚未到账的资金，当前不可作为可用经营资金">
                      <span>待结算资金</span>
                    </Tooltip>
                  }
                  value={formatCny(result.pending_settlement_cents)}
                  footnote="当前不可作为可用经营资金"
                />
              </SectionCard>
            </Col>
            <Col xs={12} lg={6}>
              <SectionCard flat bodyClassName="gew-card__body--tight">
                <MetricCard
                  label="未来 7 天收入"
                  value={formatCny(result.window_inflow_cents)}
                  tone="inflow"
                  footnote="已确认的计划收款"
                />
              </SectionCard>
            </Col>
            <Col xs={12} lg={6}>
              <SectionCard flat bodyClassName="gew-card__body--tight">
                <MetricCard
                  label="未来 7 天支出"
                  value={formatCny(result.window_outflow_cents)}
                  tone="outflow"
                  footnote="已确认的计划付款"
                />
              </SectionCard>
            </Col>
          </Row>

          <SectionCard
            title="最紧张资金时点"
            extra={<StatusTag tone="warning">限制今日可提用金额</StatusTag>}
          >
            <DescriptionGrid
              items={[
                {
                  label: '时间',
                  value: result.limiting_timestamp ? formatDateTime(result.limiting_timestamp) : '--',
                },
                {
                  label: '该时点余额',
                  value: <span className="num">{formatCny(result.limiting_balance_cents)}</span>,
                },
                {
                  label: '影响最大的事项',
                  value: result.limiting_event_title ?? '期初余额',
                },
                {
                  label: '经营留底',
                  value: <span className="num">{formatCny(result.buffer_cents)}</span>,
                },
              ]}
            />
            <div style={{ marginTop: 12 }}>
              <Space wrap>
                <Button id="reason" onClick={() => setReasonOpen(true)}>
                  查看计算依据
                </Button>
                <Button type="link" onClick={() => navigate('/analysis')}>
                  查看完整情景分析
                </Button>
              </Space>
            </div>
          </SectionCard>

          {/*
            手机端信息顺序（渐进式披露）：
            今日结论 → 当前资金 → 资金曲线与图表 → 最紧张时点 → 缺口与家庭协同
            → 资金安排参考 → 解读
            桌面端保持原有排布：图表紧随资金概览。
          */}
          {!isMobile ? moneyCharts : null}

          <Row gutter={[16, 16]}>
            <Col xs={24} lg={12}>
              <SectionCard title="缺口情况">
                <Row gutter={16}>
                  <Col span={12}>
                    <MetricCard
                      label="付款缺口"
                      value={formatCny(result.payment_gap_cents)}
                      tone={result.payment_gap_cents > 0 ? 'danger' : 'default'}
                      footnote="余额低于 0 的部分"
                    />
                  </Col>
                  <Col span={12}>
                    <MetricCard
                      label="留底缺口"
                      value={formatCny(result.buffer_gap_cents)}
                      tone={result.buffer_gap_cents > 0 ? 'danger' : 'default'}
                      footnote="余额低于留底的部分"
                    />
                  </Col>
                </Row>
                <div style={{ marginTop: 12 }}>
                  <InlineNote tone="neutral">
                    两个缺口分别计算，不能相加：留底缺口已经包含了付款缺口。
                  </InlineNote>
                </div>
              </SectionCard>
            </Col>
            <Col xs={24} lg={12}>
              <div id="share">
                <SectionCard title="家庭协同">
                  <Space wrap>
                    <Button
                      type="primary"
                      icon={<ShareAltOutlined />}
                      onClick={() => setShareOpen(true)}
                    >
                      分享给家庭
                    </Button>
                    <Button onClick={() => navigate('/family')}>进入家庭协同</Button>
                  </Space>
                </SectionCard>
              </div>
            </Col>
          </Row>

          {isMobile ? moneyCharts : null}

          {/* 资金安排参考：结算延期压力 / 未来 7 天日常收付参考 / 建议经营留底。
              顶部「资金规划」读的是本页当前口径的结果，不能读增强模块的 baseline。 */}
          <EnhancementPanel
            selectedAnalysis={result}
            selectedMode={mode}
            delayDays={delayDays}
            onDelayDaysChange={setDelayDays}
          />

          <AiExplainPanel result={result} />
        </>
      ) : null}

      <Drawer
        title="计算依据"
        width={560}
        open={reasonOpen}
        onClose={() => setReasonOpen(false)}
        destroyOnHidden
      >
        {result ? (
          <div className="gew-stack">
            <SectionCard title="基础数据" flat bodyClassName="gew-card__body--tight">
              <DescriptionGrid
                items={[
                  { label: '期初余额', value: <span className="num">{formatCny(result.opening_balance_cents)}</span> },
                  { label: '经营留底', value: <span className="num">{formatCny(result.buffer_cents)}</span> },
                  { label: '资金时点', value: formatDateTime(result.snapshot_at) },
                  { label: '分析区间', value: `${formatDateTime(result.snapshot_at)} ~ ${formatDateTime(result.window_end_at)}` },
                ]}
              />
            </SectionCard>

            <SectionCard title="余额推演" flat bodyClassName="gew-card__body--tight">
              <div className="gew-kv-list">
                {result.points.map((point) => (
                  <div className="gew-kv-list__row" key={`${point.timestamp}-${point.event_id ?? 'opening'}`}>
                    <span className="gew-kv-list__key">
                      {formatDateTime(point.timestamp)} · {point.event_title}
                    </span>
                    <span className="gew-kv-list__value num">
                      {point.is_opening ? formatCny(point.balance_cents) : `${point.delta_text} → ${point.balance_text}`}
                    </span>
                  </div>
                ))}
              </div>
            </SectionCard>

            <SectionCard title="结论" flat bodyClassName="gew-card__body--tight">
              <div className="gew-kv-list">
                <div className="gew-kv-list__row">
                  <span className="gew-kv-list__key">最紧张时点余额</span>
                  <span className="gew-kv-list__value num">{formatCny(result.limiting_balance_cents)}</span>
                </div>
                <div className="gew-kv-list__row">
                  <span className="gew-kv-list__key">扣除经营留底后可提用</span>
                  <span className="gew-kv-list__value num">{formatCny(result.max_withdrawable_cents)}</span>
                </div>
                <div className="gew-kv-list__row">
                  <span className="gew-kv-list__key">付款缺口</span>
                  <span className="gew-kv-list__value num">{formatCny(result.payment_gap_cents)}</span>
                </div>
                <div className="gew-kv-list__row">
                  <span className="gew-kv-list__key">留底缺口</span>
                  <span className="gew-kv-list__value num">{formatCny(result.buffer_gap_cents)}</span>
                </div>
              </div>
            </SectionCard>

            {result.pending_inflows_at_limit.length > 0 ? (
              <SectionCard title="最紧时点尚未到账的收入" flat bodyClassName="gew-card__body--tight">
                <div className="gew-kv-list">
                  {result.pending_inflows_at_limit.map((item) => (
                    <div className="gew-kv-list__row" key={item.event_id}>
                      <span className="gew-kv-list__key">
                        {formatDateTime(item.scheduled_at)} · {item.title}
                      </span>
                      <span className="gew-kv-list__value num">{item.amount_text}</span>
                    </div>
                  ))}
                </div>
              </SectionCard>
            ) : null}

            {result.validation_errors.length > 0 ? (
              <SectionCard title="待补充的资料" flat bodyClassName="gew-card__body--tight">
                <ul style={{ margin: 0, paddingLeft: 18 }}>
                  {result.validation_errors.map((item, index) => (
                    <li key={index}>{item.reason}</li>
                  ))}
                </ul>
              </SectionCard>
            ) : null}

            {limitEventQuery.data ? (
              <SectionCard
                title="影响最大的事项"
                flat
                bodyClassName="gew-card__body--tight"
                extra={<Button size="small" onClick={() => navigate(`/events?focus=${limitEventQuery.data!.id}`)}>查看事项<ArrowRightOutlined /></Button>}
              >
                <DescriptionGrid
                  items={[
                    { label: '事项名称', value: limitEventQuery.data.title },
                    { label: '事项编号', value: <span className="num">{limitEventQuery.data.cash_key}</span> },
                    { label: '金额', value: <span className="num">{formatCny(limitEventQuery.data.amount_cents)}</span> },
                    { label: '预计时间', value: formatDateTime(limitEventQuery.data.scheduled_at) },
                    { label: '当前版本', value: <span className="num">v{limitEventQuery.data.current_version}</span> },
                    {
                      label: '来源',
                      value: limitEventQuery.data.source_label ?? limitEventQuery.data.source_type,
                    },
                  ]}
                />
              </SectionCard>
            ) : null}
          </div>
        ) : null}
      </Drawer>

      <ShareCardDrawer
        open={shareOpen}
        onClose={() => setShareOpen(false)}
        result={result ?? null}
      />

      <Modal
        title="登记当前经营资金"
        open={snapshotOpen}
        onCancel={() => setSnapshotOpen(false)}
        onOk={() => form.submit()}
        confirmLoading={snapshotMutation.isPending}
        okText="保存"
        cancelText="取消"
        destroyOnHidden
      >
        <Form
          form={form}
          layout="vertical"
          onFinish={(values: { opening_balance: number; buffer: number }) =>
            snapshotMutation.mutate({
              opening_balance_cents: Math.round((values.opening_balance ?? 0) * 100),
              buffer_cents: Math.round((values.buffer ?? 0) * 100),
            })
          }
          initialValues={{
            opening_balance: overview ? overview.opening_balance_cents / 100 : 0,
            buffer: overview ? overview.buffer_cents / 100 : 0,
          }}
        >
          <Form.Item
            name="opening_balance"
            label="当前可用经营资金（元）"
            extra="只填写已经到账、可以立即动用的金额，待结算资金不要计入。"
            rules={[{ required: true, message: '请输入当前可用经营资金' }]}
          >
            <InputNumber
              min={0}
              precision={2}
              style={{ width: '100%' }}
              size="large"
              inputMode="decimal"
            />
          </Form.Item>
          <Form.Item
            name="buffer"
            label="经营留底（元）"
            extra="你希望始终保留在经营账户中的金额，用于覆盖临时采购、找零与突发支出。"
            rules={[{ required: true, message: '请输入经营留底金额' }]}
          >
            <InputNumber
              min={0}
              precision={2}
              style={{ width: '100%' }}
              size="large"
              inputMode="decimal"
            />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  );
}
