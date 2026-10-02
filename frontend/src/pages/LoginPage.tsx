/** 登录页。 */

import { useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { Button, Form, Input, App as AntdApp } from 'antd';
import { LockOutlined, UserOutlined } from '@ant-design/icons';
import { useMutation } from '@tanstack/react-query';

import { authApi, meApi } from '@/api/auth';
import { ApiError, errorMessage } from '@/api/client';
import { queryKeys, queryClient } from '@/api/queryClient';
import { ProductLogo } from '@/components/Brand';
import { landingPath } from '@/layouts/navigation';
import { useAuthStore } from '@/store';

interface LoginForm {
  username: string;
  password: string;
}

export default function LoginPage() {
  const navigate = useNavigate();
  const { message } = AntdApp.useApp();
  const setUser = useAuthStore((state) => state.setUser);
  const [formError, setFormError] = useState<string | null>(null);

  const mutation = useMutation({
    mutationFn: (values: LoginForm) => authApi.login(values),
    onSuccess: async () => {
      setFormError(null);
      const me = await meApi.read();
      setUser(me);
      queryClient.setQueryData(queryKeys.me, me);
      message.success(`欢迎回来，${me.display_name}`);
      navigate(landingPath(me.roles), { replace: true });
    },
    onError: (error) => {
      const text = errorMessage(error);
      setFormError(text);
      if (error instanceof ApiError && error.status >= 500) {
        message.error(text);
      }
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
        <h1>
          今天可以从经营资金里
          <br />
          拿多少钱用于家庭？
        </h1>
        <p>
          工 e 稳袋按你已确认的收付款事项，推演未来 7 天的资金变化，
          给出今天可以安全提用的金额，并说明是哪一笔付款限制了它。
        </p>
        <div className="gew-auth__list">
          <div className="gew-auth__list-item">
            <span aria-hidden="true">·</span>
            <span>当前可用资金与待结算资金分开呈现，不做合并</span>
          </div>
          <div className="gew-auth__list-item">
            <span aria-hidden="true">·</span>
            <span>每笔事项都能追溯到来源，每次修改都保留历史版本</span>
          </div>
          <div className="gew-auth__list-item">
            <span aria-hidden="true">·</span>
            <span>家庭共同决策者可查看你明确分享的协同卡片</span>
          </div>
        </div>
      </aside>

      <main className="gew-auth__form">
        <div className="gew-auth__form-inner">
          <h2 style={{ fontSize: 22, marginBottom: 4 }}>登录</h2>
          <p style={{ color: 'var(--text-secondary)', marginTop: 0, marginBottom: 24 }}>
            使用你的账户登录工 e 稳袋
          </p>

          <Form<LoginForm>
            layout="vertical"
            requiredMark={false}
            onFinish={(values) => mutation.mutate(values)}
            disabled={mutation.isPending}
          >
            <Form.Item
              name="username"
              label="账户"
              rules={[{ required: true, message: '请输入账户名' }]}
            >
              <Input
                size="large"
                prefix={<UserOutlined />}
                placeholder="请输入账户名"
                autoComplete="username"
              />
            </Form.Item>
            <Form.Item
              name="password"
              label="密码"
              rules={[{ required: true, message: '请输入密码' }]}
            >
              <Input.Password
                size="large"
                prefix={<LockOutlined />}
                placeholder="请输入密码"
                autoComplete="current-password"
              />
            </Form.Item>

            {formError ? (
              <div className="gew-inline-note gew-inline-note--danger" style={{ marginBottom: 16 }}>
                <span aria-hidden="true">×</span>
                <div>{formError}</div>
              </div>
            ) : null}

            <Button
              type="primary"
              htmlType="submit"
              size="large"
              block
              loading={mutation.isPending}
            >
              登录
            </Button>
          </Form>

          <p style={{ marginTop: 20, color: 'var(--text-secondary)', fontSize: 13 }}>
            还没有账户？<Link to="/register">立即注册</Link>
          </p>
        </div>
      </main>
    </div>
  );
}
