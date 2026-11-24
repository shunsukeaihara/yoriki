import { expect, type Page } from '@playwright/test'

// seed_dev の確認用の社員（docs/development.md §6.1）
export const MEMBER = {
  email: 'check-member@example.co.jp',
  password: process.env.YORIKI_E2E_PASSWORD ?? 'yoriki-check',
  name: '確認用の社員',
}

/** ログインした状態（Cookie）を置くファイル。auth.setup.ts が作る */
export const AUTH_FILE = 'test-results/.auth/member.json'

/** 確かめる応答（偽の LLM の答え）と、自分の依頼とを取り違えないよう、依頼ごとに違う語を付ける */
export const uniqueWord = () => `E2E-${crypto.randomUUID().slice(0, 8)}`

export const fillLogin = async (page: Page, email: string, password: string) => {
  await page.getByLabel('メールアドレス').fill(email)
  await page.getByLabel('パスワード').fill(password)
  await page.getByRole('button', { name: 'ログイン', exact: true }).click()
}

/** 機密を扱わない会話を、既定のモデル（偽モデル）で始める。会話の画面に移るまで待つ */
export const startConversation = async (page: Page, body: string) => {
  await page.goto('/conversations')
  await page.getByRole('radio', { name: '扱わない' }).click()
  await page.getByPlaceholder('依頼を入力してください').fill(body)
  await page.getByPlaceholder('依頼を入力してください').press('Enter')
  await expect(page).toHaveURL(/\/conversations\/[^/]+$/)
}

/** Run の状態の表示（Tool を使わずに完了した Run には出ない） */
export const runStatus = (page: Page) => page.getByTestId('run-status')

export const stopButton = (page: Page) => page.getByRole('button', { name: '停止', exact: true })

/** Run が終わるまで待つ（動いている間だけ「停止」が出る） */
export const expectFinished = (page: Page) => expect(stopButton(page)).toBeHidden()
