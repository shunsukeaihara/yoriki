import { defineConfig, devices } from '@playwright/test'

// E2E（docs/development.md §3.2）。起動済みの Yoriki に対して動かす。既定は手元の開発サーバー（pnpm dev）
const baseURL = process.env.YORIKI_E2E_BASE_URL ?? 'http://localhost:3000'

export default defineConfig({
  testDir: 'e2e',
  outputDir: 'test-results',
  forbidOnly: Boolean(process.env.CI),
  // Run は qcluster の worker の数までしか同時に動かないので、1つずつ動かす
  workers: 1,
  // 失敗を隠さないよう、やり直さない
  retries: 0,
  timeout: 90_000,
  // Run が進むのを待つ（Sandbox の起動を除く）
  expect: { timeout: 30_000 },
  reporter: process.env.CI ? [['github'], ['list'], ['html', { open: 'never' }]] : 'list',
  use: {
    baseURL,
    // CI の Compose は自己署名の証明書を使う
    ignoreHTTPSErrors: true,
    locale: 'ja-JP',
    timezoneId: 'Asia/Tokyo',
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  projects: [
    { name: 'setup', testMatch: 'auth.setup.ts' },
    {
      name: 'chromium',
      testMatch: '*.spec.ts',
      dependencies: ['setup'],
      use: { ...devices['Desktop Chrome'] },
    },
  ],
})
