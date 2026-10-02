/** 注册页：经营主体 / 家庭成员 / 咨询人员。 */

import { useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { App as AntdApp, Button, Form, Input, Radio, Select } from 'antd';
import { useMutation } from '@tanstack/react-query';

import { authApi, meApi, type RegisterPayload } from '@/api/auth';
import { ApiError, errorMessage } from '@/api/client';
import { queryKeys, queryClient } from '@/api/queryClient';
import { ProductLogo } from '@/components/Brand';
import { landingPath } from '@/layouts/navigation';
import { useAuthStore } from '@/store';

const BUSINESS_TYPES = [
  '个体工商户',
  '小餐饮',
  '小零售',
  '夫妻店',
  '小微批发',
  '生活服务',
  '其他',
];

interface RegisterForm {
  display_name: string;
  username: string;
  password: string;
  confirm_password: string;
  role: 'merchant' | 'family_member' | 'consultant';
  business_name?: string;
  business_type?: string;
  phone?: string;
}

export default function RegisterPage() {
  const navigate = useNavigate();
  const { message } = AntdApp.useApp();
  const setUser = useAuthStore((state) => state.setUser);
  const [role, setRole] = useState<RegisterForm['role']>('merchant');
  const [formError, setFormError] = useState<string | null>(null);

  const mutation = useMutation({
    mutationFn: async (values: RegisterForm) => {
      const payload: RegisterPayload = {
        username: values.username,
        password: values.password,
        display_name: values.display_name,
        roles: [values.role],
        phone: values.phone || null,
        business_name: values.role === 'merchant' ? values.business_name || null : null,
        business_type: values.role === 'merchant' ? values.business_type || null : null,
      };
      return authApi.register(payload);
    },
    onSuccess: async () => {
      setFormError(null);
      const me = await meApi.read();
      setUser(me);
      queryClient.setQueryData(queryKeys.me, me);
      message.success('注册成功，已为你登录');
      navigate(landingPath(me.roles), { replace: true });
    },
    onError: (error) => {
      const text = errorMessage(error);
      setFormError(text);
      if (error instanceof ApiError && error.status >= 500) message.error(text);
    },
  });

  return (
    <div className="gew-auth">
      <aside className="gew-auth__aside">
        <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
          <ProductLogo size={38} />
          <div>
            <div style={{ fontSize: 20, fontWeight: 600 }}>工 e 稳袋</div>
            <div style={{ fontSize: 12, color: 'var(--text-muted)' }}>经营资金 · 家庭协同</div>
          </div>
        </div>
        <h1>三步建立你的经营资金视图</h1>
        <p>
          注册后登记当前经营账户余额，录入未来已确认的收付款事项，
          系统会立刻给出今天可以提用的金额与最紧张的资金时点。
        </p>
        <div className="gew-auth__list">
          <div className="gew-auth__list-item">
            <span aria-hidden="true">1</span>
            <span>登记当前可用经营资金与留底要求</span>
          </div>
          <div className="gew-auth__list-item">
            <span aria-hidden="true">2</span>
            <span>录入或导入未来 7 天已确认的收付款事项</span>
          </div>
          <div className="gew-auth__list-item">
            <span aria-hidden="true">3</span>
            <span>查看今日可提用金额、最紧张时点与计算依据</span>
          </div>
        </div>
      </aside>

      <main className="gew-auth__form">
        <div className="gew-auth__form-inner">
          <h2 style={{ fontSize: 22, marginBottom: 4 }}>注册账户</h2>
          <p style={{ color: 'var(--text-secondary)', marginTop: 0, marginBottom: 20 }}>
            一个账户可以用于经营决策，也可以作为家庭成员参与协同
          </p>

          <Form<RegisterForm>
            layout="vertical"
            requiredMark={false}
            initialValues={{ role: 'merchant', business_type: '个体工商户' }}
            onFinish={(values) => mutation.mutate(values)}
            disabled={mutation.isPending}
          >
            <Form.Item name="role" label="账户用途">
              <Radio.Group
                onChange={(event) => setRole(event.target.value as RegisterForm['role'])}
                optionType="button"
                buttonStyle="solid"
              >
                <Radio.Button value="merchant">我是经营者</Radio.Button>
                <Radio.Button value="family_member">我是家庭成员</Radio.Button>
                <Radio.Button value="consultant">我是咨询人员</Radio.Button>
              </Radio.Group>
            </Form.Item>

            <Form.Item
              name="display_name"
              label="称呼"
              rules={[{ required: true, message: '请输入称呼' }, { max: 32, message: '最多 32 字' }]}
            >
              <Input size="large" placeholder="例如：王掌柜" />
            </Form.Item>

            <Form.Item
              name="username"
              label="账户名"
              rules={[
                { required: true, message: '请输入账户名' },
                { pattern: /^[A-Za-z0-9_.-]{4,32}$/, message: '4-32 位字母、数字、下划线、点或短横线' },
              ]}
            >
              <Input size="large" placeholder="用于登录，例如 wangzhanggui" autoComplete="username" />
            </Form.Item>

            {role === 'merchant' ? (
              <>
                <Form.Item
                  name="business_name"
                  label="经营名称"
                  rules={[{ required: true, message: '请填写经营名称' }, { max: 128 }]}
                >
                  <Input size="large" placeholder="例如：王记小吃店" />
                </Form.Item>
                <Form.Item name="business_type" label="经营类型">
                  <Select
                    size="large"
                    options={BUSINESS_TYPES.map((item) => ({ value: item, label: item }))}
                  />
                </Form.Item>
              </>
            ) : null}

            <Form.Item name="phone" label="手机号（选填）">
              <Input size="large" placeholder="用于账户安全验证" />
            </Form.Item>

            <Form.Item
              name="password"
              label="密码"
              rules={[
                { required: true, message: '请输入密码' },
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
              <Input.Password size="large" placeholder="至少 8 位，含字母和数字" autoComplete="new-password" />
            </Form.Item>

            <Form.Item
              name="confirm_password"
              label="确认密码"
              dependencies={['password']}
              rules={[
                { required: true, message: '请再次输入密码' },
                ({ getFieldValue }) => ({
                  validator: (_: unknown, value: string) => {
                    if (!value || getFieldValue('password') === value) return Promise.resolve();
                    return Promise.reject(new Error('两次输入的密码不一致'));
                  },
                }),
              ]}
            >
              <Input.Password size="large" placeholder="再次输入密码" autoComplete="new-password" />
            </Form.Item>

            {formError ? (
              <div className="gew-inline-note gew-inline-note--danger" style={{ marginBottom: 16 }}>
                <span aria-hidden="true">×</span>
                <div>{formError}</div>
              </div>
            ) : null}

            <Button type="primary" htmlType="submit" size="large" block loading={mutation.isPending}>
              注册并登录
            </Button>
          </Form>

          <p style={{ marginTop: 20, color: 'var(--text-secondary)', fontSize: 13 }}>
            已有账户？<Link to="/login">返回登录</Link>
          </p>
        </div>
      </main>
    </div>
  );
}
