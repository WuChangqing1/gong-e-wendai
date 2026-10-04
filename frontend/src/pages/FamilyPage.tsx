/** 家庭协同：创建家庭、管理成员、发起分享。 */

import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  Alert,
  App as AntdApp,
  Button,
  Col,
  Empty,
  Form,
  Input,
  Modal,
  Popconfirm,
  Row,
  Space,
  Table,
  Tooltip,
  Typography,
} from 'antd';
import {
  CopyOutlined,
  PlusOutlined,
  ReloadOutlined,
  ShareAltOutlined,
  UserAddOutlined,
} from '@ant-design/icons';

import { analysisApi } from '@/api/cashflow';
import { householdApi } from '@/api/household';
import { errorMessage } from '@/api/client';
import { queryKeys } from '@/api/queryClient';
import {
  DescriptionGrid,
  InlineNote,
  PageHeader,
  ResponsiveDataView,
  SectionCard,
  StatusTag,
} from '@/components/ui';
import MobileMemberList from '@/features/household/MobileMemberList';
import ShareCardDrawer from '@/features/household/ShareCardDrawer';
import type { HouseholdMember } from '@/types';
import { formatDateTime } from '@/utils/datetime';

const STATUS_TONE: Record<string, 'ok' | 'warning' | 'neutral'> = {
  active: 'ok',
  pending: 'warning',
  removed: 'neutral',
};

const STATUS_LABEL: Record<string, string> = {
  active: '已加入',
  pending: '待确认',
  removed: '已移除',
};

export default function FamilyPage() {
  const { message, modal } = AntdApp.useApp();
  const queryClient = useQueryClient();
  const [createOpen, setCreateOpen] = useState(false);
  const [shareOpen, setShareOpen] = useState(false);
  const [form] = Form.useForm<{ name: string }>();

  const householdQuery = useQuery({
    queryKey: queryKeys.household,
    queryFn: householdApi.current,
  });

  const membersQuery = useQuery({
    queryKey: [...queryKeys.household, 'members'],
    queryFn: householdApi.members,
    enabled: Boolean(householdQuery.data),
  });

  const analysisQuery = useQuery({
    queryKey: [...queryKeys.todayAnalysis, 'family'],
    queryFn: () => analysisApi.run({ mode: 'current_plan' }),
    enabled: Boolean(householdQuery.data),
  });

  const createMutation = useMutation({
    mutationFn: (values: { name: string }) => householdApi.create(values),
    onSuccess: () => {
      message.success('家庭已创建，请把邀请码发给家人');
      setCreateOpen(false);
      form.resetFields();
      queryClient.invalidateQueries({ queryKey: queryKeys.household });
    },
    onError: (error) => message.error(errorMessage(error)),
  });

  const rotateMutation = useMutation({
    mutationFn: () => householdApi.rotateInvite(),
    onSuccess: () => {
      message.success('邀请码已更新，旧邀请码立即失效');
      queryClient.invalidateQueries({ queryKey: queryKeys.household });
    },
    onError: (error) => message.error(errorMessage(error)),
  });

  const approveMutation = useMutation({
    mutationFn: (id: string) => householdApi.approve(id),
    onSuccess: () => {
      message.success('已通过加入申请');
      queryClient.invalidateQueries({ queryKey: [...queryKeys.household, 'members'] });
      queryClient.invalidateQueries({ queryKey: queryKeys.household });
    },
    onError: (error) => message.error(errorMessage(error)),
  });

  const removeMutation = useMutation({
    mutationFn: (id: string) => householdApi.remove(id),
    onSuccess: () => {
      message.success('成员已移除');
      queryClient.invalidateQueries({ queryKey: [...queryKeys.household, 'members'] });
      queryClient.invalidateQueries({ queryKey: queryKeys.household });
    },
    onError: (error) => message.error(errorMessage(error)),
  });

  const copyInvite = async () => {
    const code = householdQuery.data?.invite_code;
    if (!code) return;
    try {
      await navigator.clipboard.writeText(code);
      message.success('邀请码已复制');
    } catch {
      message.info(`邀请码：${code}`);
    }
  };

  const household = householdQuery.data;
  const members: HouseholdMember[] = membersQuery.data ?? [];
  const activeMembers = members.filter((item) => item.status === 'active');
  const pendingMembers = members.filter((item) => item.status === 'pending');

  return (
    <div className="gew-stack">
      <PageHeader
        title="家庭协同"
        subtitle="经营资金与家庭资金高度关联，但共同决策者不一定在现场。这里用于把关键结论同步给家人并取得反馈。"
        extra={
          household ? (
            <Space wrap>
              <Button icon={<ReloadOutlined />} onClick={() => householdQuery.refetch()}>
                刷新
              </Button>
              <Button
                type="primary"
                icon={<ShareAltOutlined />}
                onClick={() => setShareOpen(true)}
                disabled={activeMembers.length === 0}
              >
                分享决策卡
              </Button>
            </Space>
          ) : null
        }
      />

      {householdQuery.isLoading ? null : !household ? (
        <SectionCard>
          <Empty
            description="还没有创建家庭"
            image={Empty.PRESENTED_IMAGE_SIMPLE}
          >
            <Space direction="vertical" size={12} style={{ maxWidth: 520 }}>
              <Typography.Paragraph type="secondary" style={{ marginBottom: 0 }}>
                创建家庭后，你可以邀请家庭成员加入。家庭成员默认只能看到你明确分享的协同卡片，
                无法查看完整经营流水、账户余额或未分享的事项。
              </Typography.Paragraph>
              <Button type="primary" icon={<PlusOutlined />} onClick={() => setCreateOpen(true)}>
                创建家庭
              </Button>
            </Space>
          </Empty>
        </SectionCard>
      ) : (
        <>
          <Row gutter={[16, 16]}>
            <Col xs={24} lg={12}>
              <SectionCard title="家庭信息">
                <DescriptionGrid
                  items={[
                    { label: '家庭名称', value: household.name },
                    { label: '创建时间', value: formatDateTime(household.created_at) },
                    {
                      label: '已加入成员',
                      value: `${activeMembers.length} 人`,
                    },
                    {
                      label: '待确认申请',
                      value: `${pendingMembers.length} 人`,
                    },
                  ]}
                />

                <div style={{ marginTop: 20 }}>
                  <Typography.Text strong>邀请码</Typography.Text>
                  <div
                    style={{
                      display: 'flex',
                      alignItems: 'center',
                      gap: 12,
                      marginTop: 8,
                    }}
                  >
                    <span
                      className="num"
                      style={{
                        fontSize: 22,
                        letterSpacing: 3,
                        fontWeight: 700,
                        color: household.invite_code_active ? 'var(--brand-red)' : 'var(--text-muted)',
                      }}
                    >
                      {household.invite_code ?? '—'}
                    </span>
                    <Tooltip title="复制邀请码">
                      <Button icon={<CopyOutlined />} onClick={copyInvite} disabled={!household.invite_code}>
                        复制
                      </Button>
                    </Tooltip>
                    <Popconfirm
                      title="更新邀请码？"
                      description="更新后旧邀请码立即失效，已加入的成员不受影响。"
                      okText="确认更新"
                      cancelText="取消"
                      onConfirm={() => rotateMutation.mutate()}
                    >
                      <Button loading={rotateMutation.isPending}>更新邀请码</Button>
                    </Popconfirm>
                  </div>
                  <div style={{ marginTop: 8 }}>
                    <InlineNote tone="info">
                      家庭成员注册账户后输入邀请码提交申请，你确认后才会建立协同关系。
                      邀请码泄露时可以随时更新。
                    </InlineNote>
                  </div>
                </div>
              </SectionCard>
            </Col>

            <Col xs={24} lg={12}>
              <SectionCard title="当前可分享的结论">
                {analysisQuery.data ? (
                  <div className="gew-stack">
                    <DescriptionGrid
                      items={[
                        {
                          label: '今日可提用',
                          value: (
                            <span className="num" style={{ fontSize: 20, fontWeight: 700 }}>
                              {analysisQuery.data.max_withdrawable_cents === null
                                ? '--'
                                : `¥${(analysisQuery.data.max_withdrawable_cents / 100).toFixed(2)}`}
                            </span>
                          ),
                        },
                        {
                          label: '最紧张时间',
                          value: formatDateTime(analysisQuery.data.limiting_timestamp),
                        },
                        {
                          label: '限制原因',
                          value: analysisQuery.data.limiting_event_title ?? '—',
                        },
                        {
                          label: '风险状态',
                          value: (
                            <StatusTag tone={STATUS_TONE[analysisQuery.data.status]}>
                              {analysisQuery.data.status_label}
                            </StatusTag>
                          ),
                        },
                      ]}
                    />
                    <InlineNote>
                      分享时逐项选择要共享的内容即可。
                    </InlineNote>
                  </div>
                ) : (
                  <Empty description="正在计算结果" image={Empty.PRESENTED_IMAGE_SIMPLE} />
                )}
              </SectionCard>
            </Col>
          </Row>

          {pendingMembers.length > 0 ? (
            <Alert
              type="warning"
              showIcon
              message={`有 ${pendingMembers.length} 位家庭成员等待你的确认`}
              description="确认后对方才能看到你分享的协同卡片。"
            />
          ) : null}

          <SectionCard title="家庭成员">
            <ResponsiveDataView
              mobileCards={
                <MobileMemberList
                  members={members}
                  approvePending={approveMutation.isPending}
                  onApprove={(membershipId) => approveMutation.mutate(membershipId)}
                  onInvite={copyInvite}
                  onRemove={(row) =>
                    modal.confirm({
                      title: '确认移除成员',
                      content: `将移除 ${row.display_name}，其已查看的卡片与反馈仍保留在记录中。`,
                      okText: '确认移除',
                      cancelText: '取消',
                      onOk: () => removeMutation.mutate(row.membership_id),
                    })
                  }
                />
              }
              desktopTable={
                <Table<HouseholdMember>
                  rowKey="membership_id"
                  size="middle"
                  pagination={false}
                  dataSource={members}
                  locale={{
                    emptyText: (
                      <Empty description="还没有家庭成员加入" image={Empty.PRESENTED_IMAGE_SIMPLE}>
                        <Button icon={<UserAddOutlined />} onClick={copyInvite}>
                          复制邀请码并邀请
                        </Button>
                      </Empty>
                    ),
                  }}
                  columns={[
                    { title: '称呼', dataIndex: 'display_name' },
                    { title: '账户', dataIndex: 'username', render: (value: string) => <span className="num">{value}</span> },
                    {
                      title: '关系',
                      dataIndex: 'relation_label',
                      width: 110,
                      render: (value: string | null) => value ?? '—',
                    },
                    {
                      title: '状态',
                      dataIndex: 'status',
                      width: 110,
                      render: (value: string) => (
                        <StatusTag tone={STATUS_TONE[value] ?? 'neutral'}>
                          {STATUS_LABEL[value] ?? value}
                        </StatusTag>
                      ),
                    },
                    {
                      title: '加入时间',
                      dataIndex: 'joined_at',
                      width: 160,
                      render: (value: string | null) => (value ? formatDateTime(value) : '—'),
                    },
                    {
                      title: '操作',
                      key: 'actions',
                      width: 170,
                      render: (_value, row) => (
                        <Space size={4}>
                          {row.status === 'pending' ? (
                            <Button
                              size="small"
                              type="primary"
                              loading={approveMutation.isPending}
                              onClick={() => approveMutation.mutate(row.membership_id)}
                            >
                              通过申请
                            </Button>
                          ) : null}
                          {row.status !== 'removed' ? (
                            <Button
                              size="small"
                              danger
                              type="text"
                              onClick={() =>
                                modal.confirm({
                                  title: '确认移除成员',
                                  content: `将移除 ${row.display_name}，其已查看的卡片与反馈仍保留在记录中。`,
                                  okText: '确认移除',
                                  cancelText: '取消',
                                  onOk: () => removeMutation.mutate(row.membership_id),
                                })
                              }
                            >
                              移除
                            </Button>
                          ) : null}
                        </Space>
                      ),
                    },
                  ]}
                />
              }
            />
          </SectionCard>

        </>
      )}

      <Modal
        title="创建家庭"        open={createOpen}
        onCancel={() => setCreateOpen(false)}
        onOk={() => form.submit()}
        okText="创建"
        cancelText="取消"
        confirmLoading={createMutation.isPending}
        destroyOnHidden
      >
        <Form form={form} layout="vertical" onFinish={(values) => createMutation.mutate(values)}>
          <Form.Item
            name="name"
            label="家庭名称"
            rules={[{ required: true, message: '请输入家庭名称' }, { max: 128 }]}
          >
            <Input placeholder="例如：王家小院" size="large" />
          </Form.Item>
          <InlineNote>
            创建后会生成一次性邀请码。家庭成员使用邀请码提交申请，你确认后建立协同关系。
          </InlineNote>
        </Form>
      </Modal>

      <ShareCardDrawer
        open={shareOpen}
        onClose={() => setShareOpen(false)}
        result={analysisQuery.data ?? null}
      />
    </div>
  );
}
