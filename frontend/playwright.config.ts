import { defineConfig, devices } from '@playwright/test';

// 端到端测试。
//
// 默认直接测试同源单端口部署形态（FastAPI 同时提供 /api/* 与前端 build）——
// 这与生产环境完全一致。也可以指向 vite dev server：
//   E2E_BASE_URL=http://127.0.0.1:5173 npm run test:e2e
// 默认使用系统已安装的 Chrome（channel: 'chrome'），避免额外下载浏览器。
// 如需使用 Playwright 自带浏览器，设置 E2E_USE_BUNDLED_BROWSER=1 并执行
//   npx playwright install chromium
const USE_BUNDLED = process.env.E2E_USE_BUNDLED_BROWSER === '1';

const BASE_URL = process.env.E2E_BASE_URL || 'http://127.0.0.1:8000';

export default defineConfig({
  testDir: './e2e',
  timeout: 90_000,
  expect: { timeout: 15_000 },
  fullyParallel: false,
  retries: 0,
  workers: 1,
  reporter: [['list']],
  use: {
    baseURL: BASE_URL,
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    locale: 'zh-CN',
    timezoneId: 'Asia/Shanghai',
    actionTimeout: 15_000,
    navigationTimeout: 30_000,
  },
  projects: [
    {
      name: 'desktop',
      testIgnore: /responsive\.spec\.ts/,
      use: {
        ...devices['Desktop Chrome'],
        viewport: { width: 1440, height: 900 },
        ...(USE_BUNDLED ? {} : { channel: 'chrome' as const }),
      },
    },
    {
      // 响应式与移动端专有行为只在移动项目里跑，避免用桌面视口的断言去要求移动布局
      name: 'mobile',
      testMatch: /responsive\.spec\.ts/,
      use: {
        ...devices['Pixel 5'],
        ...(USE_BUNDLED ? {} : { channel: 'chrome' as const }),
      },
    },
  ],
});
