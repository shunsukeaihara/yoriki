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
        // パスワードでのログイン（SSO が有効なら基盤管理者だけ）
        create: (data: Login) => http.post<Session, Session>('app/v1/auth/session/', data),
        delete: () => http.delete<undefined, undefined>('app/v1/auth/session/'),
      },
      // ログイン画面に出すログインの方法（SSO の認証基盤と、パスワードの欄を出すか）
      methods: {
        get: () => http.get<AuthMethods, AuthMethods>('app/v1/auth/providers/'),
      },
      // 招待・再設定のリンク。token はリンクの URL のフラグメントにあり、本文で送る
      invitations: {
        verify: (token: string) =>
          http.post<InvitationToken, InvitationToken>('app/v1/auth/invitations/verify/', { token }),
        accept: (data: InvitationAccept) => http.post<Session, Session>('app/v1/auth/invitations/accept/', data),
      },
    },
    account: {
      get: () => http.get<MyUser, MyUser>('app/v1/my/'),
    },
    manage: {
      users: (id?: string) => ({
        ...readOnlyResource<ManageUser, ManageUserListParams>(http, 'v1/users', id),
        // 作成の応答にだけ、招待のリンク（invitation）が入る
        create: (data: ManageUserWrite) => http.post<ManageUserCreated, ManageUserCreated>('v1/users/', data),
        patch: (data: Partial<ManageUserUpdate>) => http.patch<ManageUser, ManageUser>(`v1/users/${id}/`, data),
        invitation: () => http.post<InvitationLink, InvitationLink>(`v1/users/${id}/invitation/`),
      }),
      groups: {
        list: () => http.get<Group[], Group[]>('v1/groups/'),
      },
      ssoProviders: (id?: string) => ({
        ...resource<SSOProvider, SSOProviderWrite>(http, 'v1/sso_providers', id),
        // 更新では種別を送れない
        patch: (data: Partial<SSOProviderUpdate>) =>
          http.patch<SSOProvider, SSOProvider>(`v1/sso_providers/${id}/`, data),
      }),
      llmConnections: (id?: string) => ({
        ...resource<LLMConnection, LLMConnectionWrite, LLMConnectionListParams>(http, 'v1/llm_connections', id),
        // 更新では種別と base URL（接続先）を送れない
        update: (data: LLMConnectionUpdate) =>
          http.put<LLMConnection, LLMConnection>(`v1/llm_connections/${id}/`, data),
        patch: (data: Partial<LLMConnectionUpdate>) =>
          http.patch<LLMConnection, LLMConnection>(`v1/llm_connections/${id}/`, data),
        check: () => http.post<LLMConnectionCheck, LLMConnectionCheck>(`v1/llm_connections/${id}/check/`),
      }),
      modelProfiles: (id?: string) => ({
        ...resource<ModelProfile, ModelProfileWrite, ModelProfileListParams>(http, 'v1/model_profiles', id),
        // 版は版番号で識別する
        versions: (number?: number) =>
          readOnlyResource<ModelProfileVersion>(http, `v1/model_profiles/${id}/versions`, number?.toString()),
      }),
    },
  }
}

let api: ReturnType<typeof createAPI> | null = null

export const useAPI = () => (api ??= createAPI())
