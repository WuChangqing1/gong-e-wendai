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

/**
 * 路由基路径。
 *
 * 应用既可能部署在站点根路径（`/`），也可能挂在父站点的子路径下
 * （例如 `https://example.com/wendai/`）。构建时通过 `VITE_BASE_PATH`
 * 指定基路径，Vite 会把它注入 `import.meta.env.BASE_URL`。
 *
 * 如果不把同一个基路径交给 React Router 的 `basename`，路由会把
 * `/wendai/login` 当成未知路径，直接渲染 404 页面。
 */
function routerBasename(): string {
  const base = import.meta.env.BASE_URL || '/';
  const trimmed = base.replace(/\/+$/, '');
  return trimmed === '' ? '/' : trimmed;
}

const container = document.getElementById('root');
if (!container) {
  throw new Error('未找到根节点 #root');
}

createRoot(container).render(
  <StrictMode>
    <ConfigProvider theme={antdTheme} locale={zhCN} componentSize="middle">
      <AntdApp>
        <QueryClientProvider client={queryClient}>
          <BrowserRouter basename={routerBasename()}>
            <App />
          </BrowserRouter>
        </QueryClientProvider>
      </AntdApp>
    </ConfigProvider>
  </StrictMode>,
);
