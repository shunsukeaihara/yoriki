// 画面の HTML に CSP を付ける。ビルドと起動時の環境変数（NUXT_PUBLIC_*）で中身が変わるので、返す HTML から計算する。
// サーバーの HTML には利用者の入力が入らない（ssr: false）ので、Nuxt のスクリプトだけが許可される。
// 開発サーバーは、Vite がスクリプトを差し込むので付けない
export default defineNitroPlugin((nitroApp) => {
  if (import.meta.dev) return
  nitroApp.hooks.hook('render:response', (response) => {
    if (typeof response.body !== 'string' || !response.headers) return
    if (!String(response.headers['content-type'] ?? '').startsWith('text/html')) return
    response.headers['content-security-policy'] = contentSecurityPolicy(response.body)
  })
})
