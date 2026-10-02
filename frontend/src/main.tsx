/**
 * 应用入口。
 */

import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { BrowserRouter } from 'react-router-dom';
import { QueryClientProvider } from '@tanstack/react-query';
import { App as AntdApp, ConfigProvider } from 'antd';
import zhCN from 'antd/locale/zh_CN';

import App from '@/App';
import { queryClient } from '@/api/queryClient';
import { antdTheme } from '@/styles/theme';
import '@/styles/tokens.css';

const container = document.getElementById('root');
if (!container) {
  throw new Error('未找到根节点 #root');
}

createRoot(container).render(
  <StrictMode>
    <ConfigProvider theme={antdTheme} locale={zhCN} componentSize="middle">
      <AntdApp>
        <QueryClientProvider client={queryClient}>
          <BrowserRouter>
            <App />
          </BrowserRouter>
        </QueryClientProvider>
      </AntdApp>
    </ConfigProvider>
  </StrictMode>,
);
