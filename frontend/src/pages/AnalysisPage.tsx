/** 情景分析页：多情景资金曲线、可提用金额与风险差异对比。 */

import { useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  Alert,
  App as AntdApp,
  Button,
  Collapse,
  Col,
  DatePicker,
  Empty,
  Form,
  Input,
  InputNumber,
  Modal,
  Popconfirm,
  Row,
  Segmented,
  Select,
  Space,
  Table,
  Tag,
  Tooltip,
} from 'antd';
import { DeleteOutlined, PlusOutlined, ReloadOutlined, ShareAltOutlined } from '@ant-design/icons';
import dayjs, { type Dayjs } from 'dayjs';

import { analysisApi, cashEventApi, scenarioApi } from '@/api/cashflow';
import { errorMessage } from '@/api/client';
import { queryKeys } from '@/api/queryClient';
import AnalysisChartsPanel from '@/features/analysis/AnalysisChartsPanel';
import CashflowChart from '@/components/CashflowChart';
import { InlineNote, MetricCard, PageHeader, SectionCard, StatusTag } from '@/components/ui';
import ShareCardDrawer from '@/features/household/ShareCardDrawer';
import type { AnalysisMode, AnalysisResult, AnalysisStatus, CashEvent } from '@/types';
import { formatCny, formatSigned } from '@/utils/money';
import { formatDateTime } from '@/utils/datetime';
import { STATUS_TONE } from '@/utils/labels';

const MODES: { label: string; value: AnalysisMode }[] = [
  { label: '按当前计划', value: 'current_plan' },
  { label: '到账延迟', value: 'delayed' },
  { label: '共同约束', value: 'joint' },
  { label: '自定义情景', value: 'scenarios' },
];

/**
 * 共同约束顶部提示：按状态分支，而不是按「可提用金额是否为 0」决定文案。
 *
 * 0 金额在 PAYMENT_GAP / BELOW_BUFFER 下只表示「没有安全金额」，
 * 不能写成「0 元满足所有情景」。
 */
const JOINT_ALERT_TONE: Record<AnalysisStatus, 'success' | 'warning' | 'error' | 'info'> = {
  FEASIBLE: 'success',
  PAYMENT_GAP: 'error',
  BELOW_BUFFER: 'warning',
  INPUT_INCOMPLETE: 'info',
};

function jointAlertMessage(result: AnalysisResult): string {
  switch (result.status) {
    case 'FEASIBLE':
      return `共同检查后的今日可提用：${formatCny(result.max_withdrawable_cents)}`;
    case 'PAYMENT_GAP':
      return '共同检查发现付款缺口';
    case 'BELOW_BUFFER':
      return '共同检查发现留底不足';
    default:
      return '部分数据尚未确认，暂不能形成共同结果';
  }
}

function jointAlertDescription(result: AnalysisResult): string {
  switch (result.status) {
    case 'FEASIBLE':
      return result.binding_label
        ? `按时到账与延迟到账都满足该金额，由最保守的「${result.binding_label}」决定。`
        : '所有情景都满足该金额。';
    case 'PAYMENT_GAP':
      return `同时考虑这些情况后，至少一个情景即使不提用家庭资金，仍缺 ${formatCny(
        result.payment_gap_cents,
      )} 才能覆盖已确认付款。「可提用 0 元」只表示没有安全金额，并不代表资金安排已经可行。`;
    case 'BELOW_BUFFER':
      return `同时考虑这些情况后，至少一个情景会低于经营留底，距留底还差 ${formatCny(
        result.buffer_gap_cents,
      )}。`;
    default:
      return '部分情景的收付款金额、时间或状态尚未确认，补齐资料后即可得到共同结果。';
  }
}

interface ScenarioForm {
  name: string;
  description?: string;
  cash_event_id: string;
  scheduled_at?: Dayjs;
  amount?: number;
  note?: string;
}

export default function AnalysisPage() {
  const { message } = AntdApp.useApp();
  const queryClientInstance = useQueryClient();
  const [mode, setMode] = useState<AnalysisMode>('joint');
  const [delayDays, setDelayDays] = useState(3);
  const [selected, setSelected] = useState<string[]>([]);
  const [modalOpen, setModalOpen] = useState(false);
  const [shareOpen, setShareOpen] = useState(false);
  const [form] = Form.useForm<ScenarioForm>();

  const scenarioQuery = useQuery({
    queryKey: queryKeys.scenarios,
    queryFn: scenarioApi.list,
  });

  const eventsQuery = useQuery({
    queryKey: queryKeys.cashEvents({ page: 1, page_size: 100, state: 'scheduled' }),
    queryFn: () =>
      cashEventApi.list({ page: 1, page_size: 100, state: 'scheduled' }),
  });

  const analysisQuery = useQuery({
    queryKey: [...queryKeys.todayAnalysis, 'scenario', mode, delayDays, selected.join(',')],
    queryFn: () =>
      analysisApi.run({
        mode,
        delay_days: delayDays,
        scenario_ids: mode === 'scenarios' ? selected : null,
      }),
  });

  const createMutation = useMutation({
    mutationFn: (values: ScenarioForm) => {
      if (!values.cash_event_id) throw new Error('请选择要调整的事项');
      return scenarioApi.create({
        name: values.name,
        kind: 'custom',
        description: values.description ?? null,
        overrides: [
          {
            cash_event_id: values.cash_event_id,
            scheduled_at: values.scheduled_at ? values.scheduled_at.toISOString() : null,
            amount_cents:
              values.amount === undefined || values.amount === null
                ? null
                : Math.round(values.amount * 100),
            note: values.note ?? null,
          },
        ],
      });
    },
    onSuccess: (created) => {
      message.success('情景已创建');
      setModalOpen(false);
      form.resetFields();
      queryClientInstance.invalidateQueries({ queryKey: queryKeys.scenarios });
      setMode('scenarios');
      setSelected((prev) => [...prev, created.id]);
    },
    onError: (error) => message.error(errorMessage(error)),
  });

  const deleteMutation = useMutation({
    mutationFn: (id: string) => scenarioApi.remove(id),
    onSuccess: () => {
      message.success('情景已删除');
      queryClientInstance.invalidateQueries({ queryKey: queryKeys.scenarios });
    },
    onError: (error) => message.error(errorMessage(error)),
  });

  const result: AnalysisResult | undefined = analysisQuery.data;
  const curves = useMemo(() => result?.scenarios ?? [], [result?.scenarios]);

  const rows = useMemo(
    () =>
      curves.map((curve) => ({
        key: curve.label,
        label: curve.label,
        status: curve.status,
        statusLabel: curve.status_label,
        max: curve.max_withdrawable_cents,
        limiting: curve.limiting_balance_cents,
        limitingAt: curve.limiting_timestamp,
        limitingEvent: curve.limiting_event_title,
        paymentGap: curve.payment_gap_cents,
        bufferGap: curve.buffer_gap_cents,
        inflow: curve.window_inflow_cents,
        outflow: curve.window_outflow_cents,
        end: curve.end_balance_cents,
        isBinding: result?.binding_label === curve.label,
      })),
    [curves, result?.binding_label],
  );

  const riskDiff = useMemo(() => {
    if (curves.length < 2) return null;
    const maxes = curves
      .map((item) => item.max_withdrawable_cents)
      .filter((value): value is number => value !== null);
    if (maxes.length < 2) return null;
    return { spread: Math.max(...maxes) - Math.min(...maxes) };
  }, [curves]);

  const scheduledEvents: CashEvent[] = eventsQuery.data?.items ?? [];

  return (
    <div className="gew-stack">
      <PageHeader
        title="情景分析"
        subtitle="比较不同到账情况下的资金曲线与可提用上限。情景只做假设推演，不会修改真实现金事件。"
        extra={
          <Space wrap>
            <Segmented
              options={MODES}
              value={mode}
              onChange={(value) => setMode(value as AnalysisMode)}
            />
            <Tooltip title="重新计算">
              <Button
                icon={<ReloadOutlined />}
                loading={analysisQuery.isFetching}
                onClick={() => analysisQuery.refetch()}
              />
            </Tooltip>
            <Button icon={<PlusOutlined />} onClick={() => setModalOpen(true)}>
              新建情景
            </Button>
          </Space>
        }
      />

      {mode === 'delayed' || mode === 'joint' ? (
        <SectionCard flat bodyClassName="gew-card__body--tight">
          <Space wrap align="center">
            <span style={{ color: 'var(--text-secondary)' }}>假设收款推迟天数</span>
            <InputNumber
              min={1}
              max={30}
              value={delayDays}
              onChange={(value) => setDelayDays(Number(value ?? 3))}
              addonAfter="天"
            />
            <span style={{ fontSize: 12, color: 'var(--text-muted)' }}>
              共同约束模式下，系统分别计算按时到账与延迟到账，并取两者中最保守的可提用上限。
            </span>
          </Space>
        </SectionCard>
      ) : null}

      {mode === 'scenarios' ? (
        <SectionCard title="选择要比较的情景">
          {!scenarioQuery.data || scenarioQuery.data.length === 0 ? (
            <Empty description="还没有自定义情景" image={Empty.PRESENTED_IMAGE_SIMPLE}>
              <Button type="primary" onClick={() => setModalOpen(true)}>
                新建情景
              </Button>
            </Empty>
          ) : (
            <Select
              mode="multiple"
              style={{ width: '100%' }}
              placeholder="选择情景（可多选）"
              value={selected}
              onChange={setSelected}
              options={scenarioQuery.data.map((item) => ({ value: item.id, label: item.name }))}
            />
          )}
        </SectionCard>
      ) : null}

      {analysisQuery.isError ? (
        <Alert type="error" showIcon message="分析失败" description={errorMessage(analysisQuery.error)} />
      ) : null}

      {result ? (
        <>
          {mode === 'joint' ? (
            <Alert
              type={JOINT_ALERT_TONE[result.status]}
              showIcon
              message={jointAlertMessage(result)}
              description={jointAlertDescription(result)}
            />
          ) : null}

          <SectionCard
            title="资金曲线对比"
            extra={<StatusTag tone={STATUS_TONE[result.status]}>{result.status_label}</StatusTag>}
          >
            {curves.length === 0 ? (
              <Empty description="没有可展示的情景曲线" image={Empty.PRESENTED_IMAGE_SIMPLE} />
            ) : (
              <CashflowChart scenarios={curves} bufferCents={result.buffer_cents} />
            )}
          </SectionCard>

          {/* 图表化分析：每日收支、收支结构、余额变化过程、资金积累节奏、到账分布、情景对比 */}
          <AnalysisChartsPanel analysis={result} />

          <SectionCard title="情景对比明细">
            <Table
              rowKey="key"
              size="middle"
              pagination={false}
              scroll={{ x: 1000 }}
              dataSource={rows}
              columns={[
                {
                  title: '情景',
                  dataIndex: 'label',
                  render: (value: string, row) => (
                    <Space size={6}>
                      <span style={{ fontWeight: 500 }}>{value}</span>
                      {row.isBinding ? (
                        <Tag color="red" bordered={false}>
                          最保守
                        </Tag>
                      ) : null}
                    </Space>
                  ),
                },
                {
                  title: '状态',
                  dataIndex: 'statusLabel',
                  width: 130,
                  render: (value: string, row) => (
                    <StatusTag tone={STATUS_TONE[row.status as AnalysisStatus]}>{value}</StatusTag>
                  ),
                },
                {
                  title: '今日可提用',
                  dataIndex: 'max',
                  width: 130,
                  align: 'right',
                  render: (value: number | null) => (
                    <span className="num" style={{ fontWeight: 600 }}>
                      {formatCny(value)}
                    </span>
                  ),
                },
                {
                  title: '最紧张时点',
                  dataIndex: 'limiting',
                  width: 190,
                  render: (value: number | null, row) => (
                    <div>
                      <div className="num">{formatCny(value)}</div>
                      <div style={{ fontSize: 12, color: 'var(--text-muted)' }}>
                        {formatDateTime(row.limitingAt)}
                        {row.limitingEvent ? ` · ${row.limitingEvent}` : ''}
                      </div>
                    </div>
                  ),
                },
                {
                  title: '付款缺口',
                  dataIndex: 'paymentGap',
                  width: 120,
                  align: 'right',
                  render: (value: number) => (
                    <span className="num" style={{ color: value > 0 ? 'var(--danger)' : undefined }}>
                      {formatCny(value)}
                    </span>
                  ),
                },
                {
                  title: '留底缺口',
                  dataIndex: 'bufferGap',
                  width: 120,
                  align: 'right',
                  render: (value: number) => (
                    <span className="num" style={{ color: value > 0 ? 'var(--danger)' : undefined }}>
                      {formatCny(value)}
                    </span>
                  ),
                },
                {
                  title: '期末余额',
                  dataIndex: 'end',
                  width: 130,
                  align: 'right',
                  render: (value: number | null) => <span className="num">{formatCny(value)}</span>,
                },
              ]}
              summary={() =>
                riskDiff ? (
                  <Table.Summary.Row>
                    <Table.Summary.Cell index={0} colSpan={3}>
                      风险差异（各情景可提用上限的差距）
                    </Table.Summary.Cell>
                    <Table.Summary.Cell index={3} colSpan={4}>
                      <span className="num" style={{ fontWeight: 600 }}>
                        {formatSigned(riskDiff.spread)}
                      </span>
                      <span style={{ fontSize: 12, color: 'var(--text-muted)', marginLeft: 8 }}>
                        差距越大，说明到账时间对今日决策影响越大
                      </span>
                    </Table.Summary.Cell>
                  </Table.Summary.Row>
                ) : null
              }
            />
          </SectionCard>

          <Row gutter={[16, 16]}>
            <Col xs={24} md={8}>
              <SectionCard flat bodyClassName="gew-card__body--tight">
                <MetricCard
                  label="按时到账可提用"
                  value={formatCny(
                    curves.find((item) => item.label === '按当前计划')?.max_withdrawable_cents ??
                      curves[0]?.max_withdrawable_cents ??
                      null,
                  )}
                  footnote="计划不变时的上限"
                />
              </SectionCard>
            </Col>
            <Col xs={24} md={8}>
              <SectionCard flat bodyClassName="gew-card__body--tight">
                <MetricCard
                  label="共同约束可提用"
                  value={formatCny(result.max_withdrawable_cents)}
                  footnote="所有情景同时成立的上限"
                />
              </SectionCard>
            </Col>
            <Col xs={24} md={8}>
              <SectionCard flat bodyClassName="gew-card__body--tight">
                <MetricCard
                  label="最紧张时点"
                  value={result.limiting_balance_cents === null ? '--' : formatCny(result.limiting_balance_cents)}
                  tone={result.limiting_balance_cents !== null && result.limiting_balance_cents < 0 ? 'danger' : 'default'}
                  footnote={formatDateTime(result.limiting_timestamp)}
                />
              </SectionCard>
            </Col>
          </Row>

          <div>
            <Space wrap>
              <Button type="primary" icon={<ShareAltOutlined />} onClick={() => setShareOpen(true)}>
                分享当前结论给家庭
              </Button>
            </Space>
          </div>
        </>
      ) : null}

      {scenarioQuery.data && scenarioQuery.data.length > 0 ? (
        <SectionCard title="已保存的情景">
          <Table
            rowKey="id"
            size="small"
            pagination={false}
            dataSource={scenarioQuery.data}
            columns={[
              { title: '名称', dataIndex: 'name' },
              { title: '说明', dataIndex: 'description', render: (value: string | null) => value ?? '—' },
              {
                title: '覆盖事项数',
                key: 'overrides',
                width: 120,
                render: (_value, row) => row.overrides.length,
              },
              {
                title: '操作',
                key: 'actions',
                width: 90,
                render: (_value, row) => (
                  <Popconfirm
                    title="删除该情景？"
                    description="情景只是假设推演，删除不会影响真实现金事件。"
                    onConfirm={() => deleteMutation.mutate(row.id)}
                    okText="删除"
                    cancelText="取消"
                  >
                    <Button type="text" danger size="small" icon={<DeleteOutlined />} />
                  </Popconfirm>
                ),
              },
            ]}
          />
        </SectionCard>
      ) : null}

      <Collapse
        size="small"
        items={[
          {
            key: 'notes',
            label: '说明与口径（点击展开）',
            children: (
              <div className="gew-stack">
                <InlineNote tone="info">
                  情景不会修改你的收付款事项。系统在计算时临时代入情景中的时间或金额调整，
                  每次都会重新推演未来 7 天的余额曲线。共同约束模式下，你会看到一个同时满足
                  所有情景的可提用上限——它等于各情景上限中最小的那个。
                </InlineNote>
                <InlineNote tone="neutral">
                  所有金额、时间与结论都由确定性计算引擎给出；页面上的图表只是把同一份计算结果
                  换成图形表达，不引入任何新的计算口径。
                </InlineNote>
              </div>
            ),
          },
        ]}
      />

      <Modal
        title="新建情景"
        open={modalOpen}
        onCancel={() => setModalOpen(false)}
        onOk={() => form.submit()}
        okText="创建情景"
        cancelText="取消"
        confirmLoading={createMutation.isPending}
        destroyOnHidden
        width={520}
      >
        <Form<ScenarioForm>
          form={form}
          layout="vertical"
          requiredMark={false}
          onFinish={(values) => createMutation.mutate(values)}
        >
          <Form.Item
            name="name"
            label="情景名称"
            rules={[{ required: true, message: '请输入情景名称' }, { max: 128 }]}
          >
            <Input placeholder="例如：结算推迟到第 6 天" />
          </Form.Item>
          <Form.Item
            name="cash_event_id"
            label="要调整的事项"
            rules={[{ required: true, message: '请选择要调整的事项' }]}
          >
            <Select
              showSearch
              optionFilterProp="label"
              placeholder="选择未来 7 天内的计划事项"
              options={scheduledEvents.map((item) => ({
                value: item.id,
                label: `${item.title} · ${formatCny(item.amount_cents)} · ${formatDateTime(item.scheduled_at)}`,
              }))}
            />
          </Form.Item>
          <Form.Item name="scheduled_at" label="假设到账时间">
            <DatePicker showTime format="YYYY-MM-DD HH:mm" style={{ width: '100%' }} />
          </Form.Item>
          <Form.Item
            name="amount"
            label="假设金额（元，选填）"
            extra="留空表示金额不变，只调整时间。"
          >
            <InputNumber min={0} precision={2} style={{ width: '100%' }} addonBefore="¥" />
          </Form.Item>
          <Form.Item name="description" label="说明（选填）">
            <Input placeholder="例如：客户说月底才能结清" />
          </Form.Item>
          <InlineNote>
            情景仅用于假设推演。真实数据仍以「现金事件」中的记录为准，需要正式更正时请修改事项本身，
            系统会保留版本历史并重新计算。
          </InlineNote>
        </Form>
      </Modal>

      <ShareCardDrawer
        open={shareOpen}
        onClose={() => setShareOpen(false)}
        result={result ?? null}
      />

      {analysisQuery.isLoading ? (
        <div style={{ textAlign: 'center', color: 'var(--text-muted)', padding: 24 }}>正在计算…</div>
      ) : null}

      {analysisQuery.dataUpdatedAt ? (
        <div style={{ fontSize: 12, color: 'var(--text-muted)' }}>
          计算时间 {dayjs(analysisQuery.dataUpdatedAt).format('YYYY-MM-DD HH:mm:ss')} · 引擎版本{' '}
          {result?.engine_version}
        </div>
      ) : null}
    </div>
  );
}
