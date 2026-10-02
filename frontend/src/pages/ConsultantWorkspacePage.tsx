/** 咨询人员工作台：待处理 / 处理中 / 待补充 / 已核实 / 已完成。 */

import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  App as AntdApp,
  Button,
  Col,
  Descriptions,
  Drawer,
  Empty,
  Form,
  Input,
  Row,
  Skeleton,
  Space,
  Table,
  Tag,
  Timeline,
  Typography,
} from 'antd';
import { ReloadOutlined } from '@ant-design/icons';

import { consultantApi } from '@/api/consultation';
import { errorMessage } from '@/api/client';
import { queryKeys } from '@/api/queryClient';
import { DescriptionGrid, InlineNote, MetricCard, PageHeader, SectionCard, StatusTag } from '@/components/ui';
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

const BUCKETS: { key: string; label: string }[] = [
  { key: 'pending', label: '待处理' },
  { key: 'under_review', label: '处理中' },
  { key: 'need_more_information', label: '待补充' },
  { key: 'verified', label: '已核实' },
  { key: 'closed', label: '已完成' },
];

export default function ConsultantWorkspacePage({ onlyRecords = false }: { onlyRecords?: boolean }) {
  const { message } = AntdApp.useApp();
  const queryClient = useQueryClient();
  const [bucket, setBucket] = useState<string>(onlyRecords ? 'all' : 'pending');
  const [page, setPage] = useState(1);
  const [detailId, setDetailId] = useState<string | null>(null);
  const [verifyForm] = Form.useForm<{ resolution_summary: string }>();
  const [infoForm] = Form.useForm<{ content: string }>();

  const queueQuery = useQuery({
    queryKey: queryKeys.consultantQueue({ bucket, page }),
    queryFn: () => consultantApi.queue({ bucket: bucket === 'all' ? undefined : bucket, page, page_size: 10 }),
  });

  const detailQuery = useQuery({
    queryKey: queryKeys.consultation(detailId ?? 'none'),
    queryFn: () => consultantApi.detail(detailId!),
    enabled: Boolean(detailId),
  });
  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: ['consultations'] });
  };

  const startMutation = useMutation({
    mutationFn: (id: string) => consultantApi.start(id),
    onSuccess: () => {
      message.success('已受理该事项');
      invalidate();
    },
    onError: (error) => message.error(errorMessage(error)),
  });

  const infoMutation = useMutation({
    mutationFn: ({ id, content }: { id: string; content: string }) =>
      consultantApi.requestInfo(id, content),
    onSuccess: () => {
      message.success('已要求商户补充资料');
      infoForm.resetFields();
      invalidate();
    },
    onError: (error) => message.error(errorMessage(error)),
  });

  const verifyMutation = useMutation({
    mutationFn: ({ id, summary }: { id: string; summary: string }) =>
      consultantApi.verify(id, { resolution_summary: summary }),
    onSuccess: () => {
      message.success('已填写核实结果，等待商户确认更正');
      verifyForm.resetFields();
      invalidate();
    },
    onError: (error) => message.error(errorMessage(error)),
  });

  const closeMutation = useMutation({
    mutationFn: (id: string) => consultantApi.close(id, {}),
    onSuccess: () => {
      message.success('事项已完成');
      invalidate();
    },
    onError: (error) => message.error(errorMessage(error)),
  });

  const detail = detailQuery.data;
  const items = queueQuery.data?.items ?? [];

  const columns = [
    {
      title: '事项编号',
      dataIndex: 'case_no',
      width: 150,
      render: (value: string) => <span className="num">{value}</span>,
    },
    {
      title: '问题类型',
      dataIndex: 'question_type',
      width: 200,
      render: (value: string) => QUESTION_TYPE_LABELS[value] ?? value,
    },
    {
      title: '事项名称',
      dataIndex: 'shared_fields',
      render: (value: Record<string, unknown>) => String(value?.event_title ?? '—'),
    },
    {
      title: '相关金额',
      dataIndex: 'shared_fields',
      width: 130,
      align: 'right' as const,
      render: (value: Record<string, unknown>) =>
        typeof value?.amount_cents === 'number' ? (
          <span className="num">{formatCny(value.amount_cents as number)}</span>
        ) : (
          '—'
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
          受理
        </Button>
      ),
    },
  ];

  return (
    <div className="gew-stack">
      <PageHeader
        title={onlyRecords ? '事项记录' : '咨询工作台'}
        subtitle="核实商户提交的经营资金事项。你可以改变状态、要求补充资料、填写核实结果，但不能直接修改商户的收付款事项。"
        extra={
          <Button
            icon={<ReloadOutlined />}
            loading={queueQuery.isFetching}
            onClick={() => queueQuery.refetch()}
          >
            刷新
          </Button>
        }
      />

      <Row gutter={[16, 16]}>
        <Col xs={24} md={8}>
          <SectionCard flat bodyClassName="gew-card__body--tight">
            <MetricCard
              label="当前筛选下的事项"
              value={String(queueQuery.data?.meta.total ?? 0)}
              footnote={bucket === 'all' ? '全部记录' : BUCKETS.find((item) => item.key === bucket)?.label}
            />
          </SectionCard>
        </Col>
        <Col xs={24} md={16}>
          <SectionCard flat bodyClassName="gew-card__body--tight">
            <Space wrap>
              {[{ key: 'all', label: '全部' }, ...BUCKETS].map((item) => (
                <Button
                  key={item.key}
                  size="small"
                  type={bucket === item.key ? 'primary' : 'default'}
                  onClick={() => {
                    setBucket(item.key);
                    setPage(1);
                  }}
                >
                  {item.label}
                </Button>
              ))}
            </Space>
          </SectionCard>
        </Col>
      </Row>

      <SectionCard title="咨询事项">
        <Table<ConsultationCase>
          rowKey="id"
          size="middle"
          columns={columns}
          dataSource={items}
          loading={queueQuery.isLoading}
          scroll={{ x: 980 }}
          locale={{
            emptyText: <Empty description="该分组下暂无事项" image={Empty.PRESENTED_IMAGE_SIMPLE} />,
          }}
          pagination={{
            current: queueQuery.data?.meta.page ?? 1,
            pageSize: 10,
            total: queueQuery.data?.meta.total ?? 0,
            showSizeChanger: false,
            onChange: setPage,
          }}
        />
      </SectionCard>

      <SectionCard title="权限说明">
        <InlineNote tone="info">
          你只能看到商户授权共享的字段。家庭评论、家庭成员资料、未授权经营事件、完整经营余额、
          当前最大可提用金额与家庭留底设置均不可见，也无法修改商户的收付款事项。
        </InlineNote>
      </SectionCard>

      <Drawer
        title={detail ? `受理事项 · ${detail.case_no}` : '受理事项'}
        width={680}
        open={Boolean(detailId)}
        onClose={() => setDetailId(null)}
        destroyOnHidden
      >
        {detailQuery.isLoading ? <Skeleton active paragraph={{ rows: 6 }} /> : null}

        {detail ? (
          <div className="gew-stack">
            <Space wrap>
              <StatusTag tone={CONSULTATION_STATUS_TONE[detail.status]}>
                {CONSULTATION_STATUS_LABELS[detail.status]}
              </StatusTag>
              <Tag bordered={false}>{QUESTION_TYPE_LABELS[detail.question_type]}</Tag>
            </Space>

            <SectionCard title="商户授权可见的字段" flat bodyClassName="gew-card__body--tight">
              <DescriptionGrid
                items={[
                  { label: '事项名称', value: String(detail.shared_fields.event_title ?? '—') },
                  {
                    label: '事项类型',
                    value: EVENT_TYPE_LABELS[String(detail.shared_fields.event_type)] ?? '—',
                  },
                  {
                    label: '相关金额',
                    value:
                      typeof detail.shared_fields.amount_cents === 'number'
                        ? formatCny(detail.shared_fields.amount_cents as number)
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
            </SectionCard>

            <SectionCard title="商户问题" flat bodyClassName="gew-card__body--tight">
              <Typography.Paragraph style={{ whiteSpace: 'pre-wrap', marginBottom: 0 }}>
                {detail.question}
              </Typography.Paragraph>
            </SectionCard>

            <SectionCard title="处理动作" flat bodyClassName="gew-card__body--tight">
              <Space direction="vertical" size={16} style={{ width: '100%' }}>
                <Space wrap>
                  <Button
                    type="primary"
                    disabled={detail.status !== 'submitted'}
                    loading={startMutation.isPending}
                    onClick={() => startMutation.mutate(detail.id)}
                  >
                    受理并开始处理
                  </Button>
                </Space>

                <div>
                  <Typography.Text strong>要求补充资料</Typography.Text>
                  <Form
                    form={infoForm}
                    layout="vertical"
                    onFinish={(values: { content: string }) =>
                      infoMutation.mutate({ id: detail.id, content: values.content })
                    }
                    style={{ marginTop: 8 }}
                  >
                    <Form.Item
                      name="content"
                      rules={[{ required: true, message: '请说明需要补充的资料' }]}
                    >
                      <Input.TextArea
                        rows={3}
                        maxLength={1000}
                        placeholder="例如：请提供该笔结算的结算单截图编号或结算批次号。"
                      />
                    </Form.Item>
                    <Button htmlType="submit" loading={infoMutation.isPending}>
                      提交补充要求
                    </Button>
                  </Form>
                </div>

                <div>
                  <Typography.Text strong>填写核实结果</Typography.Text>
                  <Form
                    form={verifyForm}
                    layout="vertical"
                    onFinish={(values: { resolution_summary: string }) =>
                      verifyMutation.mutate({ id: detail.id, summary: values.resolution_summary })
                    }
                    style={{ marginTop: 8 }}
                  >
                    <Form.Item
                      name="resolution_summary"
                      rules={[{ required: true, message: '请填写核实结果' }]}
                    >
                      <Input.TextArea
                        rows={4}
                        maxLength={2000}
                        placeholder="例如：该笔结算已于 10 月 3 日 15:20 完成，实际到账 2358.60 元，预计当日入账。"
                      />
                    </Form.Item>
                    <Button type="primary" htmlType="submit" loading={verifyMutation.isPending}>
                      标记为已核实
                    </Button>
                  </Form>
                </div>

                <div>
                  <Button
                    disabled={!['verified', 'under_review'].includes(detail.status)}
                    loading={closeMutation.isPending}
                    onClick={() => closeMutation.mutate(detail.id)}
                  >
                    完成事项
                  </Button>
                </div>

                <InlineNote tone="warning">
                  核实结果只会作为商户的参考。是否更正收付款事项由商户确认，系统会保留版本历史并重新计算。
                </InlineNote>
              </Space>
            </SectionCard>

            <SectionCard title="处理时间线" flat bodyClassName="gew-card__body--tight">
              {detail.updates.length === 0 ? (
                <div style={{ color: 'var(--text-muted)' }}>暂无处理记录</div>
              ) : (
                <Timeline
                  items={detail.updates.map((item) => ({
                    color: item.to_status === 'need_more_information' ? 'orange' : 'blue',
                    children: (
                      <div>
                        <div style={{ fontSize: 13 }}>{item.content ?? '状态更新'}</div>
                        <div style={{ fontSize: 12, color: 'var(--text-muted)' }}>
                          {item.actor_name ?? '系统'} · {formatDateTime(item.created_at)}
                        </div>
                      </div>
                    ),
                  }))}
                />
              )}
            </SectionCard>

            <Descriptions size="small" column={1}>
              <Descriptions.Item label="咨询渠道">
                {detail.status === 'closed' ? '已完成' : '内部受理'}
              </Descriptions.Item>
            </Descriptions>
          </div>
        ) : null}
      </Drawer>
    </div>
  );
}
