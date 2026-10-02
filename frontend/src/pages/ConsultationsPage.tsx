/** 经营咨询：商户发起咨询、查看处理结果、根据结果更正事项。 */

import { useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  Alert,
  App as AntdApp,
  Button,
  Descriptions,
  Drawer,
  Empty,
  Form,
  Input,
  Modal,
  Select,
  Skeleton,
  Space,
  Table,
  Tag,
  Timeline,
  Typography,
} from 'antd';
import { PlusOutlined, ReloadOutlined, RobotOutlined } from '@ant-design/icons';

import { cashEventApi } from '@/api/cashflow';
import { consultationApi } from '@/api/consultation';
import { aiApi } from '@/api/ai';
import { AI_FALLBACK_MESSAGE, errorMessage } from '@/api/client';
import { queryKeys } from '@/api/queryClient';
import { DescriptionGrid, InlineNote, PageHeader, SectionCard, StatusTag } from '@/components/ui';
import RevisionDrawer from '@/features/events/RevisionDrawer';
import type { ConsultationCase, ConsultationStatus } from '@/types';
import { formatCny } from '@/utils/money';
import { formatDateTime } from '@/utils/datetime';
import {
  CONSULTATION_STATUS_LABELS,
  CONSULTATION_STATUS_TONE,
  EVENT_TYPE_LABELS,
  QUESTION_TYPE_LABELS,
  STATE_LABELS,
} from '@/utils/labels';

const STATUS_OPTIONS: { value: ConsultationStatus; label: string }[] = Object.entries(
  CONSULTATION_STATUS_LABELS,
).map(([value, label]) => ({ value: value as ConsultationStatus, label }));

export default function ConsultationsPage() {
  const { message } = AntdApp.useApp();
  const queryClientInstance = useQueryClient();
  const [page, setPage] = useState(1);
  const [status, setStatus] = useState<ConsultationStatus | undefined>();
  const [createOpen, setCreateOpen] = useState(false);
  const [detailId, setDetailId] = useState<string | null>(null);
  const [revisionId, setRevisionId] = useState<string | null>(null);
  const [drafting, setDrafting] = useState(false);
  const [form] = Form.useForm<{
    cash_event_id: string;
    question_type: string;
    question: string;
  }>();

  const listQuery = useQuery({
    queryKey: queryKeys.consultations({ page, status }),
    queryFn: () => consultationApi.list({ page, page_size: 10, status }),
  });

  const eventsQuery = useQuery({
    queryKey: queryKeys.cashEvents({ page: 1, page_size: 100, forConsultation: true }),
    queryFn: () => cashEventApi.list({ page: 1, page_size: 100 }),
  });

  const detailQuery = useQuery({
    queryKey: queryKeys.consultation(detailId ?? 'none'),
    queryFn: () => consultationApi.detail(detailId!),
    enabled: Boolean(detailId),
  });

  const createMutation = useMutation({
    mutationFn: (values: { cash_event_id: string; question_type: string; question: string }) =>
      consultationApi.create({ ...values, status: 'submitted' }),
    onSuccess: () => {
      message.success('咨询已提交，咨询人员会尽快受理');
      setCreateOpen(false);
      form.resetFields();
      queryClientInstance.invalidateQueries({ queryKey: ['consultations'] });
    },
    onError: (error) => message.error(errorMessage(error)),
  });

  const applyUpdateMutation = useMutation({
    mutationFn: (caseId: string) => consultationApi.approveUpdate(caseId),
    onSuccess: (data) => {
      message.success(`已根据咨询结果更正事项，当前版本 v${data.new_version}，已触发重新计算`);
      queryClientInstance.invalidateQueries({ queryKey: ['cash-events'] });
      queryClientInstance.invalidateQueries({ queryKey: ['consultations'] });
      queryClientInstance.invalidateQueries({ queryKey: queryKeys.analysisStale });
    },
    onError: (error) => message.error(errorMessage(error)),
  });

  const draftWithAi = async () => {
    const values = form.getFieldsValue();
    if (!values.cash_event_id || !values.question_type) {
      message.warning('请先选择事项与问题类型');
      return;
    }
    setDrafting(true);
    try {
      const allowed = await consultationApi.allowedFields(values.cash_event_id);
      const result = await aiApi.draftConsultation({
        question_type: values.question_type,
        question: values.question ?? '',
        fields: allowed.preview,
      });
      form.setFieldValue('question', result.draft);
      message.success('已整理，请确认后再提交');
    } catch (error) {
      message.warning(errorMessage(error) || AI_FALLBACK_MESSAGE);
    } finally {
      setDrafting(false);
    }
  };

  const columns = [
    {
      title: '事项编号',
      dataIndex: 'case_no',
      width: 150,
      render: (value: string) => <span className="num">{value}</span>,
    },
    {
      title: '咨询内容',
      dataIndex: 'question',
      render: (value: string, row: ConsultationCase) => (
        <div>
          <div style={{ fontWeight: 500 }}>{QUESTION_TYPE_LABELS[row.question_type] ?? row.question_type}</div>
          <div style={{ fontSize: 12, color: 'var(--text-muted)' }}>
            {value.length > 60 ? `${value.slice(0, 60)}…` : value}
          </div>
        </div>
      ),
    },
    {
      title: '状态',
      dataIndex: 'status',
      width: 130,
      render: (value: ConsultationStatus) => (
        <StatusTag tone={CONSULTATION_STATUS_TONE[value]}>
          {CONSULTATION_STATUS_LABELS[value]}
        </StatusTag>
      ),
    },
    {
      title: '提交时间',
      dataIndex: 'submitted_at',
      width: 160,
      render: (value: string | null) => (value ? formatDateTime(value) : '—'),
    },
    {
      title: '操作',
      key: 'actions',
      width: 100,
      render: (_value: unknown, row: ConsultationCase) => (
        <Button size="small" type="link" onClick={() => setDetailId(row.id)}>
          查看详情
        </Button>
      ),
    },
  ];

  const detail = detailQuery.data;

  const corrections = useMemo(() => {
    if (!detail) return [];
    const fields = detail.resolution_fields ?? {};
    return Object.entries(fields).map(([key, value]) => ({ key, value }));
  }, [detail]);

  return (
    <div className="gew-stack">
      <PageHeader
        title="经营咨询"
        subtitle="某一笔结算、到账或经营资金事项不明确时，整理最小必要信息发起咨询，收到结果后再更新收付款事项。"
        extra={
          <Space wrap>
            <Button
              icon={<ReloadOutlined />}
              loading={listQuery.isFetching}
              onClick={() => listQuery.refetch()}
            >
              刷新
            </Button>
            <Button type="primary" icon={<PlusOutlined />} onClick={() => setCreateOpen(true)}>
              发起咨询
            </Button>
          </Space>
        }
      />

      <InlineNote tone="info">
        咨询只会共享最小必要字段（事项编号、类型、名称、金额、预计时间、当前状态、来源摘要、
        你的问题与事项版本）。完整经营余额、家庭信息、可提用金额与留底金额不会被共享。
      </InlineNote>

      <SectionCard
        title="我的咨询"
        extra={
          <Select
            allowClear
            placeholder="按状态筛选"
            style={{ width: 160 }}
            value={status}
            onChange={(value) => {
              setStatus(value);
              setPage(1);
            }}
            options={STATUS_OPTIONS}
          />
        }
      >
        <Table<ConsultationCase>
          rowKey="id"
          size="middle"
          columns={columns}
          dataSource={listQuery.data?.items ?? []}
          loading={listQuery.isLoading}
          scroll={{ x: 800 }}
          locale={{
            emptyText: (
              <Empty description="还没有咨询记录" image={Empty.PRESENTED_IMAGE_SIMPLE}>
                <Button type="primary" onClick={() => setCreateOpen(true)}>
                  发起第一条咨询
                </Button>
              </Empty>
            ),
          }}
          pagination={{
            current: listQuery.data?.meta.page ?? 1,
            pageSize: 10,
            total: listQuery.data?.meta.total ?? 0,
            showSizeChanger: false,
            onChange: setPage,
          }}
        />
      </SectionCard>

      <Modal
        title="发起经营咨询"
        open={createOpen}
        width={640}
        onCancel={() => setCreateOpen(false)}
        onOk={() => form.submit()}
        okText="提交咨询"
        cancelText="取消"
        confirmLoading={createMutation.isPending}
        destroyOnHidden
      >
        <Form
          form={form}
          layout="vertical"
          requiredMark={false}
          onFinish={(values) => createMutation.mutate(values)}
        >
          <Form.Item
            name="cash_event_id"
            label="选择要咨询的收付款事项"
            rules={[{ required: true, message: '请选择事项' }]}
          >
            <Select
              showSearch
              optionFilterProp="label"
              placeholder="选择事项"
              options={(eventsQuery.data?.items ?? []).map((item) => ({
                value: item.id,
                label: `${item.title} · ${formatCny(item.amount_cents)} · ${formatDateTime(item.scheduled_at)}`,
              }))}
            />
          </Form.Item>

          <Form.Item
            name="question_type"
            label="问题类型"
            rules={[{ required: true, message: '请选择问题类型' }]}
          >
            <Select
              placeholder="选择问题类型"
              options={Object.entries(QUESTION_TYPE_LABELS).map(([value, label]) => ({
                value,
                label,
              }))}
            />
          </Form.Item>

          <Form.Item
            name="question"
            label="你的问题"
            rules={[{ required: true, message: '请描述你的问题' }, { max: 2000 }]}
            extra="描述越具体，咨询人员越容易核实。"
          >
            <Input.TextArea
              rows={4}
              maxLength={2000}
              showCount
              placeholder="例如：这笔 2358.60 元的结算款原定 10 月 3 日到账，但账户还没有收到，想确认结算进度。"
            />
          </Form.Item>

          <Space>
            <Button icon={<RobotOutlined />} loading={drafting} onClick={draftWithAi}>
              帮我整理成规范描述
            </Button>
            <span style={{ fontSize: 12, color: 'var(--text-muted)' }}>
              智能整理只使用允许共享的字段，不会发送完整流水。整理后你可以直接修改。
            </span>
          </Space>
        </Form>
      </Modal>

      <Drawer
        title={detail ? `咨询详情 · ${detail.case_no}` : '咨询详情'}
        width={640}
        open={Boolean(detailId)}
        onClose={() => setDetailId(null)}
        destroyOnHidden
      >
        {detailQuery.isLoading ? <Skeleton active paragraph={{ rows: 5 }} /> : null}

        {detail ? (
          <div className="gew-stack">
            <Space wrap>
              <StatusTag tone={CONSULTATION_STATUS_TONE[detail.status]}>
                {CONSULTATION_STATUS_LABELS[detail.status]}
              </StatusTag>
              <span style={{ fontSize: 12, color: 'var(--text-muted)' }}>
                提交于 {formatDateTime(detail.submitted_at ?? detail.created_at)}
              </span>
            </Space>

            <SectionCard title="咨询内容" flat bodyClassName="gew-card__body--tight">
              <div style={{ marginBottom: 12 }}>
                <Tag bordered={false}>{QUESTION_TYPE_LABELS[detail.question_type]}</Tag>
              </div>
              <Typography.Paragraph style={{ whiteSpace: 'pre-wrap', marginBottom: 0 }}>
                {detail.question}
              </Typography.Paragraph>
            </SectionCard>

            <SectionCard title="共享给咨询人员的字段" flat bodyClassName="gew-card__body--tight">
              <DescriptionGrid
                items={[
                  { label: '事项编号', value: <span className="num">{detail.case_no}</span> },
                  {
                    label: '事项类型',
                    value: EVENT_TYPE_LABELS[String(detail.shared_fields.event_type)] ?? '—',
                  },
                  { label: '事项名称', value: String(detail.shared_fields.event_title ?? '—') },
                  {
                    label: '相关金额',
                    value:
                      typeof detail.shared_fields.amount_cents === 'number'
                        ? formatCny(detail.shared_fields.amount_cents)
                        : '—',
                  },
                  {
                    label: '预计时间',
                    value: formatDateTime(String(detail.shared_fields.scheduled_at ?? '')),
                  },
                  {
                    label: '当前状态',
                    value:
                      STATE_LABELS[String(detail.shared_fields.event_state) as 'scheduled'] ?? '—',
                  },
                  { label: '来源摘要', value: String(detail.shared_fields.source_summary ?? '—') },
                  {
                    label: '事项版本',
                    value: <span className="num">v{String(detail.shared_fields.event_version ?? '—')}</span>,
                  },
                ]}
              />
              <div style={{ marginTop: 12 }}>
                <InlineNote>
                  未共享：经营账户完整余额、家庭信息、家庭评论、可提用金额、留底金额、完整现金流曲线。
                </InlineNote>
              </div>
            </SectionCard>

            {detail.resolution_summary ? (
              <SectionCard title="处理结果" flat bodyClassName="gew-card__body--tight">
                <Typography.Paragraph style={{ whiteSpace: 'pre-wrap' }}>
                  {detail.resolution_summary}
                </Typography.Paragraph>
                {corrections.length > 0 ? (
                  <Descriptions size="small" column={1} bordered>
                    {corrections.map((item) => (
                      <Descriptions.Item key={item.key} label={item.key}>
                        {String(item.value)}
                      </Descriptions.Item>
                    ))}
                  </Descriptions>
                ) : null}

                {detail.cash_event_id ? (
                  <Space style={{ marginTop: 12 }} wrap>
                    <Button
                      type="primary"
                      loading={applyUpdateMutation.isPending}
                      onClick={() => applyUpdateMutation.mutate(detail.id)}
                    >
                      根据结果更正事项并重新计算
                    </Button>
                    <Button onClick={() => setRevisionId(detail.cash_event_id)}>查看事项版本</Button>
                  </Space>
                ) : null}
                <div style={{ marginTop: 12 }}>
                  <InlineNote tone="warning">
                    更正必须由你确认。咨询人员不能直接修改你的收付款事项；确认后系统会保存旧版本并重新计算。
                  </InlineNote>
                </div>
              </SectionCard>
            ) : null}

            <SectionCard title="处理时间线" flat bodyClassName="gew-card__body--tight">
              {detail.updates.length === 0 ? (
                <div style={{ color: 'var(--text-muted)' }}>暂无处理记录</div>
              ) : (
                <Timeline
                  items={detail.updates.map((item) => ({
                    color: item.to_status === 'need_more_information' ? 'orange' : 'blue',
                    children: (
                      <div>
                        <div style={{ fontSize: 13 }}>
                          {item.content ?? '状态更新'}
                        </div>
                        <div style={{ fontSize: 12, color: 'var(--text-muted)' }}>
                          {item.actor_name ?? '系统'}
                          {item.actor_role ? `（${item.actor_role}）` : ''} ·{' '}
                          {formatDateTime(item.created_at)}
                          {item.to_status
                            ? ` · ${CONSULTATION_STATUS_LABELS[item.to_status as ConsultationStatus] ?? item.to_status}`
                            : ''}
                        </div>
                      </div>
                    ),
                  }))}
                />
              )}
            </SectionCard>

            {detail.status === 'closed' ? (
              <Alert type="success" showIcon message="该咨询事项已完成" />
            ) : null}
          </div>
        ) : null}
      </Drawer>

      <RevisionDrawer
        eventId={revisionId}
        open={Boolean(revisionId)}
        onClose={() => setRevisionId(null)}
      />
    </div>
  );
}
