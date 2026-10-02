/** 404 页面。 */

import { Button, Result } from 'antd';
import { useNavigate } from 'react-router-dom';

import { landingPath } from '@/layouts/navigation';
import { useAuthStore } from '@/store';

export default function NotFoundPage() {
  const navigate = useNavigate();
  const user = useAuthStore((state) => state.user);

  return (
    <div
      style={{
        minHeight: '100vh',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        background: 'var(--page-bg)',
      }}
    >
      <Result
        status="404"
        title="页面不存在"
        subTitle="你访问的地址可能已经变更，或者链接输入有误。"
        extra={
          <Button type="primary" onClick={() => navigate(user ? landingPath(user.roles) : '/login')}>
            {user ? '返回首页' : '去登录'}
          </Button>
        }
      />
    </div>
  );
}
