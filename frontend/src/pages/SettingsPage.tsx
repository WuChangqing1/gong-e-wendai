/** 我的：个人资料、经营资料、留底设置、家庭设置、安全设置、智能服务状态。 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  App as AntdApp,
  Button,
  Col,
  Form,
  Input,
  InputNumber,
  Row,
  Select,
  Skeleton,
  Space,
  Tabs,
  Tag,
  Typography,
} from 'antd';
import { useNavigate } from 'react-router-dom';

import { authApi, meApi, merchantApi } from '@/api/auth';
import { aiApi } from '@/api/ai';
import { householdApi } from '@/api/household';
import { errorMessage } from '@/api/client';
import { queryKeys, queryClient } from '@/api/queryClient';
import { DescriptionGrid, InlineNote, PageHeader, SectionCard, StatusTag } from '@/components/ui';
import { useAuthStore, useUiStore } from '@/store';
import { formatCny } from '@/utils/money';
import { formatDateTime } from '@/utils/datetime';
import { ROLE_LABELS, SOURCE_LABELS } from '@/utils/labels';

const BUSINESS_TYPES = ['个体工商户', '小餐饮', '小零售', '夫妻店', '小微批发', '生活服务', '其他'];

export default function SettingsPage() {
  const navigate = useNavigate();
  const { message } = AntdApp.useApp();
  const queryClientInstance = useQueryClient();
  const user = useAuthStore((state) => state.user);
  const clear = useAuthStore((state) => state.clear);
  const setUser = useAuthStore((state) => state.setUser);
  const toggleSidebar = useUiStore((state) => state.toggleSidebar);
  const collapsed = useUiStore((state) => state.sidebarCollapsed);
  const [snapshotForm] = Form.useForm<{ opening_balance: number; buffer: number }>();

  const isMerchant = Boolean(user?.roles.includes('merchant'));

  const profileQuery = useQuery({
    queryKey: queryKeys.merchantProfile,
    queryFn: merchantApi.profile,
    enabled: isMerchant,
  });

  const overviewQuery = useQuery({
    queryKey: queryKeys.accountOverview,
    queryFn: merchantApi.overview,
    enabled: isMerchant,
  });

  const snapshotsQuery = useQuery({
    queryKey: queryKeys.accountSnapshots,
    queryFn: () => merchantApi.snapshots(10),
    enabled: isMerchant,
  });

  const householdQuery = useQuery({
    queryKey: queryKeys.household,
    queryFn: householdApi.current,
    enabled: isMerchant,
  });

  const aiQuery = useQuery({
    queryKey: queryKeys.aiStatus,
    queryFn: aiApi.status,
  });

  const sessionsQuery = useQuery({
    queryKey: [...queryKeys.me, 'sessions'],
    queryFn: meApi.sessions,
  });

  const profileMutation = useMutation({
    mutationFn: (values: Record<string, unknown>) => meApi.update(values),
    onSuccess: (data) => {
      message.success('个人资料已更新');
      setUser(data);
      queryClientInstance.setQueryData(queryKeys.me, data);
    },
    onError: (error) => message.error(errorMessage(error)),
  });

  const merchantMutation = useMutation({
    mutationFn: (values: {
      business_name?: string;
      business_type?: string;
      contact_name?: string;
      phone_optional?: string;
      account_name?: string;
      account_masked_no?: string;
    }) => merchantApi.updateProfile(values),
    onSuccess: () => {
      message.success('经营资料已更新');
      queryClientInstance.invalidateQueries({ queryKey: queryKeys.merchantProfile });
      queryClientInstance.invalidateQueries({ queryKey: queryKeys.me });
    },
    onError: (error) => message.error(errorMessage(error)),
  });

  const bufferMutation = useMutation({
    mutationFn: (values: { opening_balance: number; buffer: number }) =>
      merchantApi.createSnapshot({
        opening_balance_cents: Math.round((values.opening_balance ?? 0) * 100),
      }).then(() =>
        merchantApi.updateProfile({
          default_buffer_amount_cents: Math.round((values.buffer ?? 0) * 100),
        }),
      ),
    onSuccess: () => {
      message.success('资金时点与留底设置已保存');
      queryClientInstance.invalidateQueries({ queryKey: queryKeys.accountOverview });
      queryClientInstance.invalidateQueries({ queryKey: queryKeys.accountSnapshots });
      queryClientInstance.invalidateQueries({ queryKey: queryKeys.analysisStale });
    },
    onError: (error) => message.error(errorMessage(error)),
  });

  const passwordMutation = useMutation({
    mutationFn: (values: { current_password: string; new_password: string }) =>
      meApi.changePassword(values),
    onSuccess: () => {
      message.success('密码已更新，请重新登录');
      clear();
      queryClient.clear();
      navigate('/login', { replace: true });
    },
    onError: (error) => message.error(errorMessage(error)),
  });

  const logoutMutation = useMutation({
    mutationFn: () => authApi.logout(),
    onSettled: () => {
      clear();
      queryClient.clear();
      navigate('/login', { replace: true });
    },
  });

  const [passwordForm] = Form.useForm<{
    current_password: string;
    new_password: string;
    confirm_password: string;
  }>();

  const personalTab = (
    <Row gutter={[16, 16]}>
      <Col xs={24} lg={12}>
        <SectionCard title="个人资料">
          {user ? (
            <Form
              layout="vertical"
              initialValues={{
                display_name: user.display_name,
                phone: user.phone ?? '',
                email: user.email ?? '',
              }}
              onFinish={(values) => profileMutation.mutate(values)}
            >
              <Form.Item label="账户名">
                <Input value={user.username} disabled />
              </Form.Item>
              <Form.Item
                name="display_name"
                label="称呼"
                rules={[{ required: true, message: '请输入称呼' }, { max: 32 }]}
              >
                <Input />
              </Form.Item>
              <Form.Item name="phone" label="手机号">
                <Input placeholder="选填" />
              </Form.Item>
              <Form.Item name="email" label="邮箱">
                <Input placeholder="选填" />
              </Form.Item>
              <Button type="primary" htmlType="submit" loading={profileMutation.isPending}>
                保存
              </Button>
            </Form>
          ) : (
            <Skeleton active />
          )}
        </SectionCard>
      </Col>
      <Col xs={24} lg={12}>
        <SectionCard title="账户信息">
          <DescriptionGrid
            items={[
              { label: '账户名', value: user?.username ?? '—' },
              { label: '业务角色', value: (user?.roles ?? []).map((role) => ROLE_LABELS[role]).join('、') || '—' },
              { label: '注册时间', value: formatDateTime(user?.created_at) },
              { label: '最近登录', value: formatDateTime(user?.last_login_at) },
              {
                label: '当前有效会话',
                value: <span className="num">{sessionsQuery.data?.active_sessions ?? '—'}</span>,
              },
            ]}
          />
          <div style={{ marginTop: 16 }}>
            <Space wrap>
              <Button onClick={() => toggleSidebar()}>
                {collapsed ? '展开' : '收起'}左侧导航
              </Button>
              <Button danger onClick={() => logoutMutation.mutate()} loading={logoutMutation.isPending}>
                退出登录
              </Button>
            </Space>
          </div>
        </SectionCard>
      </Col>
    </Row>
  );

  const merchantTab = isMerchant ? (
    <Row gutter={[16, 16]}>
      <Col xs={24} lg={12}>
        <SectionCard title="经营资料">
          {profileQuery.data ? (
            <Form
              layout="vertical"
              initialValues={{
                business_name: profileQuery.data.business_name,
                business_type: profileQuery.data.business_type,
                contact_name: profileQuery.data.contact_name ?? '',
                phone_optional: profileQuery.data.phone_optional ?? '',
                account_name: profileQuery.data.account_name,
                account_masked_no: profileQuery.data.account_masked_no ?? '',
              }}
              onFinish={(values) => merchantMutation.mutate(values)}
            >
              <Form.Item
                name="business_name"
                label="经营名称"
                rules={[{ required: true, message: '请输入经营名称' }, { max: 128 }]}
              >
                <Input />
              </Form.Item>
              <Form.Item name="business_type" label="经营类型">
                <Select options={BUSINESS_TYPES.map((item) => ({ value: item, label: item }))} />
              </Form.Item>
              <Form.Item name="contact_name" label="联系人">
                <Input />
              </Form.Item>
              <Form.Item name="phone_optional" label="经营联系电话">
                <Input placeholder="选填" />
              </Form.Item>
              <Form.Item name="account_name" label="核心经营收款账户名称">
                <Input placeholder="例如：经营收款账户" />
              </Form.Item>
              <Form.Item
                name="account_masked_no"
                label="账户尾号"
                extra="只保存脱敏尾号，例如 8821。不要填写完整卡号。"
              >
                <Input placeholder="例如：8821" maxLength={64} />
              </Form.Item>
              <Button type="primary" htmlType="submit" loading={merchantMutation.isPending}>
                保存经营资料
              </Button>
            </Form>
          ) : (
            <Skeleton active />
          )}
        </SectionCard>
      </Col>

      <Col xs={24} lg={12}>
        <SectionCard title="经营留底与资金时点">
          <Form
            form={snapshotForm}
            layout="vertical"
            initialValues={{
              opening_balance: overviewQuery.data ? overviewQuery.data.opening_balance_cents / 100 : 0,
              buffer: overviewQuery.data ? overviewQuery.data.buffer_cents / 100 : 0,
            }}
            onFinish={(values) => bufferMutation.mutate(values)}
          >
            <Form.Item
              name="opening_balance"
              label="当前可用经营资金（元）"
              extra="只填写已经到账、可以立即动用的金额。"
              rules={[{ required: true, message: '请输入当前可用经营资金' }]}
            >
              <InputNumber min={0} precision={2} style={{ width: '100%' }} />
            </Form.Item>
            <Form.Item
              name="buffer"
              label="默认经营留底（元）"
              extra="系统在计算可提用金额时，会保证未来 7 天任何时点的余额都不低于该金额。"
              rules={[{ required: true, message: '请输入经营留底金额' }]}
            >
              <InputNumber min={0} precision={2} style={{ width: '100%' }} />
            </Form.Item>
            <Button type="primary" htmlType="submit" loading={bufferMutation.isPending}>
              保存并重新计算
            </Button>
          </Form>

          <div style={{ marginTop: 16 }}>
            <InlineNote tone="info">
              待结算资金不会并入当前可用经营资金。请只登记已结算、可立即动用的余额。
            </InlineNote>
          </div>
        </SectionCard>

        <div style={{ marginTop: 16 }}>
          <SectionCard title="最近资金时点">
            {(snapshotsQuery.data ?? []).length === 0 ? (
              <Typography.Text type="secondary">还没有登记记录</Typography.Text>
            ) : (
              <div className="gew-kv-list">
                {(snapshotsQuery.data ?? []).map((item) => (
                  <div className="gew-kv-list__row" key={item.id}>
                    <span className="gew-kv-list__key">
                      {formatDateTime(item.snapshot_at)} · {SOURCE_LABELS[item.source_type as 'manual'] ?? item.source_type}
                    </span>
                    <span className="gew-kv-list__value num">
                      {formatCny(item.opening_balance_cents)}
                    </span>
                  </div>
                ))}
              </div>
            )}
          </SectionCard>
        </div>
      </Col>
    </Row>
  ) : (
    <SectionCard>
      <Typography.Text type="secondary">当前账户不是经营主体账户，没有经营资料。</Typography.Text>
    </SectionCard>
  );

  const securityTab = (
    <Row gutter={[16, 16]}>
      <Col xs={24} lg={12}>
        <SectionCard title="修改密码">
          <Form
            form={passwordForm}
            layout="vertical"
            onFinish={(values) =>
              passwordMutation.mutate({
                current_password: values.current_password,
                new_password: values.new_password,
              })
            }
          >
            <Form.Item
              name="current_password"
              label="当前密码"
              rules={[{ required: true, message: '请输入当前密码' }]}
            >
              <Input.Password autoComplete="current-password" />
            </Form.Item>
            <Form.Item
              name="new_password"
              label="新密码"
              rules={[
                { required: true, message: '请输入新密码' },
                { min: 8, message: '密码至少 8 位' },
                {
                  validator: (_, value: string) => {
                    if (!value) return Promise.resolve();
                    if (/^\d+$/.test(value) || /^[A-Za-z]+$/.test(value)) {
                      return Promise.reject(new Error('密码需要同时包含字母和数字'));
                    }
                    return Promise.resolve();
                  },
                },
              ]}
            >
              <Input.Password autoComplete="new-password" />
            </Form.Item>
            <Form.Item
              name="confirm_password"
              label="确认新密码"
              dependencies={['new_password']}
              rules={[
                { required: true, message: '请再次输入新密码' },
                ({ getFieldValue }) => ({
                  validator: (_: unknown, value: string) => {
                    if (!value || getFieldValue('new_password') === value) return Promise.resolve();
                    return Promise.reject(new Error('两次输入的密码不一致'));
                  },
                }),
              ]}
            >
              <Input.Password autoComplete="new-password" />
            </Form.Item>
            <Button type="primary" htmlType="submit" loading={passwordMutation.isPending}>
              更新密码
            </Button>
          </Form>
          <div style={{ marginTop: 12 }}>
            <InlineNote tone="warning">
              修改密码后所有登录会话都会失效，需要使用新密码重新登录。
            </InlineNote>
          </div>
        </SectionCard>
      </Col>
      <Col xs={24} lg={12}>
        <SectionCard title="安全设置">
          <div className="gew-kv-list">
            <div className="gew-kv-list__row">
              <span className="gew-kv-list__key">密码存储</span>
              <span className="gew-kv-list__value">Argon2 哈希，系统不保存明文密码</span>
            </div>
            <div className="gew-kv-list__row">
              <span className="gew-kv-list__key">登录凭证</span>
              <span className="gew-kv-list__value">HttpOnly + SameSite=Lax Cookie</span>
            </div>
            <div className="gew-kv-list__row">
              <span className="gew-kv-list__key">当前有效会话</span>
              <span className="gew-kv-list__value num">
                {sessionsQuery.data?.active_sessions ?? '—'}
              </span>
            </div>
          </div>
          <div style={{ marginTop: 16 }}>
            <InlineNote tone="info">
              智能服务的密钥只保存在后端环境变量中，任何时候都不会下发给页面。
              页面上无法查看、也无法获取 API Key。
            </InlineNote>
          </div>
        </SectionCard>
      </Col>
    </Row>
  );

  const householdTab = isMerchant ? (
    <SectionCard title="家庭设置">
      {householdQuery.data ? (
        <div className="gew-stack">
          <DescriptionGrid
            items={[
              { label: '家庭名称', value: householdQuery.data.name },
              {
                label: '已加入成员',
                value: `${householdQuery.data.members.filter((item) => item.status === 'active').length} 人`,
              },
              {
                label: '待确认申请',
                value: `${householdQuery.data.members.filter((item) => item.status === 'pending').length} 人`,
              },
              { label: '创建时间', value: formatDateTime(householdQuery.data.created_at) },
            ]}
          />
          <Space>
            <Button type="primary" onClick={() => navigate('/family')}>
              进入家庭协同
            </Button>
          </Space>
          <InlineNote>
            家庭成员只能看到你明确分享的协同卡片，无法访问收付款事项、经营账户余额与分析全量数据。
          </InlineNote>
        </div>
      ) : (
        <Space direction="vertical">
          <Typography.Text type="secondary">还没有创建家庭。</Typography.Text>
          <Button type="primary" onClick={() => navigate('/family')}>
            去创建家庭
          </Button>
        </Space>
      )}
    </SectionCard>
  ) : (
    <SectionCard>
      <Typography.Text type="secondary">当前账户不是经营主体账户。</Typography.Text>
    </SectionCard>
  );

  const aiTab = (
    <SectionCard title="智能服务状态">
      <div className="gew-kv-list">
        <div className="gew-kv-list__row">
          <span className="gew-kv-list__key">当前状态</span>
          <span className="gew-kv-list__value">
            {aiQuery.data?.available ? (
              <StatusTag tone="ok">可用</StatusTag>
            ) : aiQuery.data?.enabled ? (
              <StatusTag tone="warning">已启用但暂不可用</StatusTag>
            ) : (
              <StatusTag tone="neutral">未启用</StatusTag>
            )}
          </span>
        </div>
        <div className="gew-kv-list__row">
          <span className="gew-kv-list__key">服务模型</span>
          <span className="gew-kv-list__value">{aiQuery.data?.model ?? '未配置'}</span>
        </div>
        <div className="gew-kv-list__row">
          <span className="gew-kv-list__key">密钥位置</span>
          <span className="gew-kv-list__value">仅后端环境变量，页面不可见</span>
        </div>
      </div>
      <div style={{ marginTop: 16 }}>
        <InlineNote tone="info">
          智能服务只负责理解、提取、整理与表达。所有金额、时间、余额、可提用金额与缺口都由确定性计算引擎给出，
          智能服务不会重新计算，也不会修改计算结果。服务不可用时，你仍然可以手动完成全部操作。
        </InlineNote>
      </div>
      <div style={{ marginTop: 12 }}>
        <Tag bordered={false}>智能录入</Tag>
        <Tag bordered={false}>帮我讲清楚</Tag>
        <Tag bordered={false}>咨询描述整理</Tag>
      </div>
    </SectionCard>
  );

  return (
    <div className="gew-stack">
      <PageHeader title="我的" subtitle="个人资料、经营资料、留底设置、家庭设置与安全设置" />

      <Tabs
        items={[
          { key: 'personal', label: '个人资料', children: personalTab },
          { key: 'merchant', label: '经营资料', children: merchantTab },
          { key: 'household', label: '家庭设置', children: householdTab },
          { key: 'security', label: '安全设置', children: securityTab },
          { key: 'ai', label: '智能服务', children: aiTab },
        ]}
      />
    </div>
  );
}
