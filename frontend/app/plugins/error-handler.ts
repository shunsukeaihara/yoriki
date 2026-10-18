import { AxiosError } from 'axios'

/**
 * 画面で処理されなかった API のエラーを、エラー画面にする（docs/frontend-conventions.md §3.5）。
 * フォームの送信のように画面で表示するエラーは、createSubmit 等で処理するのでここには来ない。
 */
export default defineNuxtPlugin({
  name: 'error-handler',
  setup(nuxtApp) {
    /** AxiosError ならエラー画面にして true を返す */
    const handle = (error: unknown): boolean => {
      if (!(error instanceof AxiosError)) return false
      const statusCode = error.response?.status ?? 500
      // 401 は API クライアントがログイン画面へ移動させる
      if (statusCode === 401) return true
      nuxtApp.runWithContext(() => showError({ statusCode, data: { detail: makeErrorMsg(error) } }))
      return true
    }

    // コンポーネントの setup・描画・イベントハンドラーで起きたエラー
    nuxtApp.hook('vue:error', (error) => {
      handle(error)
    })
    // それ以外（await しなかった Promise 等）
    window.addEventListener('unhandledrejection', (event) => {
      if (handle(event.reason)) event.preventDefault()
    })
  },
})
