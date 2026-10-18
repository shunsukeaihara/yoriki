// 未ログインならログイン画面へ。ログイン後は元のページへ戻る。
// ログインの要らないページ（ログイン画面、開発用ページ）は definePageMeta({ auth: false }) を付ける
export default defineNuxtRouteMiddleware((to) => {
  if (to.meta.auth === false) return
  const account = useAccountStore()
  if (!account.isAuthenticated) return navigateTo({ path: '/login', query: { next: to.fullPath } })
})
