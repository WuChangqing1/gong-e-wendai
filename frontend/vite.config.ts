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
    chunkSizeWarningLimit: 1500,
    rollupOptions: {
      output: {
        manualChunks: {
          react: ['react', 'react-dom', 'react-router-dom'],
          antd: ['antd', '@ant-design/icons'],
          charts: ['echarts'],
        },
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
