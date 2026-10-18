// 画面を表示する前に、ログイン状態を確認する（docs/frontend-conventions.md §4）
export default defineNuxtPlugin({
  name: 'auth',
  async setup() {
    try {
      await useAccountStore().init()
    } catch (error) {
      // API に届かないときは未ログインとして扱い、ログイン画面で改めて表示する
      console.error(error)
    }
  },
})
