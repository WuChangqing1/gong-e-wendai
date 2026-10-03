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

/**
 * 是否为远程目标（公网 HTTPS 入口）。
 *
 * 远程目标的每一次请求都要经过公网往返，而且每个用例都会新建商户账号，
 * 触发 Argon2 哈希（CPU 密集）。本机默认的 15 秒动作超时在公网下会偶发超时，
 * 表现为随机的登录/保存失败——服务端日志却是 0 错误、负载接近 0。
 * 因此对远程目标放宽超时，同时保留重试，避免把网络抖动误报成产品缺陷。
 */
const IS_REMOTE = !/^https?:\/\/(127\.0\.0\.1|localhost|\[::1\])(:|\/|$)/.test(BASE_URL);
const SCALE = IS_REMOTE ? 2 : 1;

export default defineConfig({
  testDir: './e2e',
  timeout: 90_000 * SCALE,
  expect: { timeout: 15_000 * SCALE },
  fullyParallel: false,
  retries: IS_REMOTE ? 1 : 0,
  workers: 1,
  reporter: [['list']],
  use: {
    baseURL: BASE_URL,
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    locale: 'zh-CN',
    timezoneId: 'Asia/Shanghai',
    actionTimeout: 15_000 * SCALE,
    navigationTimeout: 30_000 * SCALE,
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
