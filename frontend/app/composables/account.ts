/** ログイン状態とログインユーザー。画面からは isAuthenticated、user、init()、logout() だけを使う */
export const useAccountStore = defineStore('account', () => {
  const user = ref<MyUser | null>(null)
  const isAuthenticated = computed(() => user.value !== null)

  /** 起動時に呼ぶ。CSRF の Cookie を受け取り、ログイン中ならユーザーを取得する */
  const init = async () => {
    const api = useAPI()
    const session = await api.auth.session.get()
    user.value = session.isAuthenticated ? await api.account.get() : null
  }

  /** Yoriki のセッションだけを消す（前段の認証プロキシのセッションは消さない） */
  const logout = async () => {
    await useAPI().auth.session.delete()
    user.value = null
    await navigateTo('/login')
  }

  return { user, isAuthenticated, init, logout }
})
