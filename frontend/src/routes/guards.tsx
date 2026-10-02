/** 路由守卫。 */

import type { ReactNode } from 'react';
import { Navigate, useLocation } from 'react-router-dom';
import { Spin } from 'antd';
import { useQuery } from '@tanstack/react-query';

import { meApi } from '@/api/auth';
import { queryKeys } from '@/api/queryClient';
import { landingPath } from '@/layouts/navigation';
import { useAuthStore } from '@/store';
import { Result, Button } from 'antd';
import { useNavigate } from 'react-router-dom';
import type { Role } from '@/types';

function FullPageSpin() {
  return (
    <div
      style={{
        minHeight: '60vh',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
      }}
    >
      <Spin tip="正在加载…" size="large">
        <div style={{ width: 120, height: 60 }} />
      </Spin>
    </div>
  );
}

export function RequireAuth({ children }: { children: ReactNode }) {
  const location = useLocation();
  const { isPending, isError } = useQuery({
    queryKey: queryKeys.me,
    queryFn: meApi.read,
    retry: false,
  });
  const user = useAuthStore((state) => state.user);

  if (isPending) return <FullPageSpin />;
  if (isError || !user) {
    return <Navigate to="/login" replace state={{ from: location.pathname }} />;
  }
  return <>{children}</>;
}

export function RequireGuest({ children }: { children: ReactNode }) {
  const user = useAuthStore((state) => state.user);
  if (user) {
    return <Navigate to={landingPath(user.roles)} replace />;
  }
  return <>{children}</>;
}

export function RequireRole({ roles, children }: { roles: Role[]; children: ReactNode }) {
  const user = useAuthStore((state) => state.user);
  const navigate = useNavigate();

  if (!user) return <Navigate to="/login" replace />;
  if (!roles.some((role) => user.roles.includes(role))) {
    return (
      <Result
        status="403"
        title="没有访问权限"
        subTitle="当前账户没有访问该页面的权限。"
        extra={
          <Button type="primary" onClick={() => navigate(landingPath(user.roles))}>
            返回首页
          </Button>
        }
      />
    );
  }
  return <>{children}</>;
}
