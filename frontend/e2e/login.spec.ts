import { expect, test } from '@playwright/test'
import { fillLogin, MEMBER } from './support'

test('ログインしていなければログインの画面へ移り、ログインすると元の画面に戻り、ログアウトできる', async ({ page }) => {
  await page.goto('/skills')
  await expect(page).toHaveURL(/\/login\?/)

  await fillLogin(page, MEMBER.email, MEMBER.password)
  await expect(page).toHaveURL(/\/skills$/)
  await expect(page.getByText(MEMBER.name, { exact: true })).toBeVisible()

  await page.getByRole('button', { name: 'ログアウト' }).click()
  await expect(page).toHaveURL(/\/login/)
  await page.goto('/skills')
  await expect(page).toHaveURL(/\/login\?/)
})

test('誤ったパスワードではログインできない', async ({ page }) => {
  await page.goto('/login')
  // ログインの失敗の回数で確認用の社員を止めないよう、いないアカウントで確かめる
  await fillLogin(page, `e2e-${Date.now()}@example.co.jp`, 'wrong-password')
  await expect(page.getByText('メールアドレスかパスワードが正しくありません。')).toBeVisible()
  await expect(page).toHaveURL(/\/login$/)
})
