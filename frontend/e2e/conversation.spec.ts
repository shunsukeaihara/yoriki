import { readFile } from 'node:fs/promises'
import { expect, test } from '@playwright/test'
import { AUTH_FILE, expectFinished, runStatus, startConversation, stopButton, uniqueWord } from './support'

// 応答は偽の LLM の既定の台本（docs/development.md §6.2）が決める
test.use({ storageState: AUTH_FILE })

test('会話を始めて応答を受け、続けて依頼できる', async ({ page }) => {
  const [first, second] = [uniqueWord(), uniqueWord()]
  await startConversation(page, `${first} を調べて`)
  await expect(page.getByText(`（偽のLLM）「${first} を調べて」を受け取りました。`)).toBeVisible()
  await expectFinished(page)

  const prompt = page.getByRole('textbox')
  await prompt.fill(`${second} も調べて`)
  await prompt.press('Enter')
  await expect(page.getByText(`（偽のLLM）「${second} も調べて」を受け取りました。`)).toBeVisible()
  await expectFinished(page)

  // 開き直しても同じ表示に戻る
  await page.reload()
  await expect(page.getByText(`（偽のLLM）「${first} を調べて」を受け取りました。`)).toBeVisible()
  await expect(page.getByText(`（偽のLLM）「${second} も調べて」を受け取りました。`)).toBeVisible()
})

test('動いている Run を停止できる', async ({ page }) => {
  await startConversation(page, `${uniqueWord()} [long]`)
  // 書きかけの応答が少しずつ届く（SSE）
  await expect(page.getByText('2行目を')).toBeVisible()
  await expect(page.getByText('長い答えの終わりです。')).toBeHidden()
  await stopButton(page).click()
  await expect(runStatus(page)).toHaveText('停止中')
  // 停止は区切り（応答を受け終えたところ）で効く（harness.md §4.2）
  await expect(runStatus(page)).toHaveText('停止済み')
  await expectFinished(page)
})

test('確認に回答すると Run が再開する', async ({ page }) => {
  await startConversation(page, `${uniqueWord()} [ask]`)
  await expect(page.getByRole('paragraph').filter({ hasText: 'どの月の請求書を確かめますか。' })).toBeVisible()
  await page.getByRole('radio', { name: '11月' }).click()
  await page.getByRole('button', { name: '回答する' }).click()
  await expect(page.getByText('回答を受け取りました。')).toBeVisible()
  await expect(runStatus(page)).toHaveText('完了')
})

test('Sandbox で書いた成果物をダウンロードできる', async ({ page }) => {
  test.setTimeout(240_000)
  const word = uniqueWord()
  await startConversation(page, `${word} [file]`)
  // Sandbox の起動を待つ
  await expect(page.getByText('/workspace/output/result.md に書きました。')).toBeVisible({ timeout: 180_000 })
  await expect(runStatus(page)).toHaveText('完了')

  // 広い画面では、添付と成果物の欄が開いている
  const download = page.waitForEvent('download')
  await page.getByRole('link', { name: /result\.md/ }).click()
  const path = await (await download).path()
  expect(await readFile(path, 'utf8')).toContain(`${word} [file]`)
})
