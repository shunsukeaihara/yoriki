import { expect, test } from '@playwright/test'
import { AUTH_FILE } from './support'

test.use({ storageState: AUTH_FILE })

test('CSP が画面を妨げず、許可していないインラインのスクリプトを止める', async ({ page }) => {
  const violations: string[] = []
  await page.exposeFunction('reportViolation', (text: string) => violations.push(text))
  await page.addInitScript(() => {
    document.addEventListener('securitypolicyviolation', (e) => {
      void (window as unknown as { reportViolation: (text: string) => void }).reportViolation(
        `${e.effectiveDirective} ${e.blockedURI}`,
      )
    })
  })

  const response = await page.goto('/conversations')
  const policy = response?.headers()['content-security-policy']
  // 開発サーバー（pnpm dev）は CSP を付けない
  test.skip(!policy, 'CSP のない構成')
  expect(policy).toMatch(/script-src 'self' 'sha256-/)
  expect(policy).not.toMatch(/script-src[^;]*'unsafe-inline'/)

  await expect(page.getByRole('radio', { name: '扱わない' })).toBeVisible()
  for (const path of ['/skills', '/tasks', '/runs', '/usage']) {
    await page.goto(path)
    await expect(page.getByRole('button', { name: 'ログアウト' })).toBeVisible()
  }
  expect(violations).toEqual([])

  await page.evaluate(() => {
    const script = document.createElement('script')
    script.textContent = 'window.__injected = true'
    document.body.append(script)
  })
  expect(await page.evaluate(() => '__injected' in window)).toBe(false)
  await expect.poll(() => violations).toEqual(['script-src-elem inline'])
})
