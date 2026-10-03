import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import { fileURLToPath, URL } from 'node:url';

/**
 * 构建基路径。
 *
 * * 同源单端口部署（开发调试、独立端口 18088）：保持 `/`
 * * 挂在父站点子路径下（例如 `https://example.com/wendai/`）：设为 `/wendai/`
 *
 * 为什么要可配置：当应用挂在父站点子路径时，如果静态资源仍使用根路径的
 * `/assets/...`，就会与父站点上其他应用的 `/assets/` 冲突（实测会被对方的
 * 后端返回 404）。使用命名空间基路径可以让资源与父站点完全隔离。
 *
 * 用法：`VITE_BASE_PATH=/wendai/ npm run build`
 */
const base = process.env.VITE_BASE_PATH || '/';

export default defineConfig({
  base,
  plugins: [react()],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  server: {
    port: 5173,
    strictPort: false,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: false,
      },
    },
  },
  build: {
    outDir: 'dist',
    sourcemap: false,
    chunkSizeWarningLimit: 1800,
    rollupOptions: {
      output: {
        /**
         * 不用 manualChunks 指定 antd / react。
         *
         * 实测结论（同一份代码，只改分包方式）：
         *
         * | 方案 | 每个页面下载的 js | 请求数 |
         * | --- | --- | --- |
         * | 固定 antd + react | 1628 KB | 3 |
         * | 再加 rc-picker / dayjs 单独成块 | 1630 KB | 2~5 |
         *
         * antd 是运行时依赖，被应用外壳（布局、按钮、表单）直接引用，
         * 本来就是每个入口都必然加载的共享依赖；手动切成多块只会产生
         * 重复引用与额外请求。交给 Rollup 自动合并即可。
         *
         * 真正需要减掉的是**按路由才用到**的大块 —— 那部分由
         * `components/charts/lazy.tsx` 通过动态 import 拆分（图表约 542 KB），
         * 使家庭协同 / 我的等页面不再下载 ECharts。
         */
      },
    },
  },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./tests/setup.ts'],
    include: ['tests/**/*.test.{ts,tsx}'],
    css: false,
  },
} as never);
