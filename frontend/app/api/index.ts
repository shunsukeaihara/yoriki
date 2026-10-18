const createAPI = () => {
  const config = useRuntimeConfig()
  const { $i18n } = useNuxtApp()
  const http = createHttp(config.public.apiEndpoint, () => $i18n.locale.value, {
    // 未ログインならログイン画面へ。ログイン後は元のページへ戻る
    onUnauthorized: async () => {
      const current = useRouter().currentRoute.value
      if (current.path !== '/login') await navigateTo({ path: '/login', query: { next: current.fullPath } })
    },
  })

  return {
    http,
    // リソースは backend の ViewSet ごとに追加する（docs/frontend-conventions.md §3.2）
    auth: {
      session: {
        // ログイン状態を返し、CSRF の Cookie を受け取る
        get: () => http.get<Session, Session>('app/v1/auth/session/'),
        delete: () => http.delete<undefined, undefined>('app/v1/auth/session/'),
      },
      providers: {
        list: () => http.get<AuthProvider[], AuthProvider[]>('app/v1/auth/providers/'),
      },
    },
    account: {
      get: () => http.get<MyUser, MyUser>('app/v1/my/'),
    },
  }
}

let api: ReturnType<typeof createAPI> | null = null

export const useAPI = () => (api ??= createAPI())
