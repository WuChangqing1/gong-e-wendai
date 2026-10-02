/** 应用根组件：会话初始化 + 路由。 */

import { useEffect } from 'react';
import { useQuery } from '@tanstack/react-query';
import { App as AntdApp } from 'antd';

import { meApi } from '@/api/auth';
import { queryKeys } from '@/api/queryClient';
import { ApiError } from '@/api/client';
import AppRoutes from '@/routes/AppRoutes';
import { useAuthStore } from '@/store';

export default function App() {
  const { message } = AntdApp.useApp();
  const setUser = useAuthStore((state) => state.setUser);
  const setInitialised = useAuthStore((state) => state.setInitialised);
  const initialised = useAuthStore((state) => state.initialised);

  const { data, isError, error, isFetched } = useQuery({
    queryKey: queryKeys.me,
    queryFn: meApi.read,
    retry: false,
    staleTime: 60_000,
  });

  useEffect(() => {
    if (data) {
      setUser(data);
    } else if (isFetched && isError) {
      if (error instanceof ApiError && error.status >= 500) {
        message.error('服务暂时不可用，请稍后重试');
      }
      setUser(null);
    }
  }, [data, isError, error, isFetched, setUser, message]);

  useEffect(() => {
    if (!initialised) setInitialised(true);
  }, [initialised, setInitialised]);

  return <AppRoutes />;
}
