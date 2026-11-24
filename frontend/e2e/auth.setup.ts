import { expect, test as setup } from '@playwright/test'
import { AUTH_FILE, fillLogin, MEMBER } from './support'

// ログインの画面からログインし、その状態をほかのテストで使い回す
setup('ログインする', async ({ page }) => {
  await page.goto('/login')
  await fillLogin(page, MEMBER.email, MEMBER.password)
  await expect(page).toHaveURL(/\/conversations$/)
  await page.context().storageState({ path: AUTH_FILE })
})
