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
    // 会話で選べるモデル設定。選択肢として全件を返す（ページングしない）
    modelProfiles: {
      list: () => http.get<AppModelProfile[], AppModelProfile[]>('app/v1/model_profiles/'),
    },
    conversations: (id?: string) => ({
      ...resource<Conversation, ConversationWrite, ConversationListParams>(http, 'app/v1/conversations', id),
      // AppConversationMessageViewSet は list と create だけ
      messages: () => ({
        list: (params?: ListParams) =>
          http.get<Paginated<Message>, Paginated<Message>>(`app/v1/conversations/${id}/messages/`, { params }),
        create: (data: MessageWrite) => http.post<Message, Message>(`app/v1/conversations/${id}/messages/`, data),
      }),
      // 添付と成果物。添付は multipart で送る（メッセージで送るまでは一覧に出ない）
      artifacts: (aid?: string) => ({
        ...readOnlyResource<Artifact, ArtifactListParams>(http, `app/v1/conversations/${id}/artifacts`, aid),
        upload: (file: File) => {
          const form = new FormData()
          form.append('file', file)
          return http.post<Artifact, Artifact>(`app/v1/conversations/${id}/artifacts/`, form, {
            timeout: UPLOAD_TIMEOUT,
          })
        },
        // ブラウザのリンクで開く（ダウンロードとして返る）
        downloadUrl: () => `${config.public.apiEndpoint}app/v1/conversations/${id}/artifacts/${aid}/download/`,
      }),
    }),
    // Skill（design.md §3）。中身の編集は Draft で行い、公開すると版になる
    skills: (id?: string) => ({
      ...readOnlyResource<Skill, SkillListParams>(http, 'app/v1/skills', id),
      create: (data: SkillCreate) => http.post<Skill, Skill>('app/v1/skills/', data),
      // 名前だけを変えられる（公開の権限が要る）
      patch: (data: Partial<SkillWrite>) => http.patch<Skill, Skill>(`app/v1/skills/${id}/`, data),
      delete: () => http.delete<undefined, undefined>(`app/v1/skills/${id}/`),
      publish: (data: SkillPublish) => http.post<SkillVersion, SkillVersion>(`app/v1/skills/${id}/publish/`, data),
      rollback: (data: SkillRollback) => http.post<SkillVersion, SkillVersion>(`app/v1/skills/${id}/rollback/`, data),
      suspend: (data: SkillSuspend) => http.post<Skill, Skill>(`app/v1/skills/${id}/suspend/`, data),
      resume: (data: SkillSuspend) => http.post<Skill, Skill>(`app/v1/skills/${id}/resume/`, data),
      // 版は版番号で識別する。一覧は中身を含まない
      versions: (number?: number) => ({
        list: (params?: ListParams) =>
          http.get<Paginated<MinimumSkillVersion>, Paginated<MinimumSkillVersion>>(`app/v1/skills/${id}/versions/`, {
            params,
          }),
        get: () => http.get<SkillVersion, SkillVersion>(`app/v1/skills/${id}/versions/${number}/`),
      }),
      // AppSkillChangeViewSet は list だけ
      changes: () => ({
        list: (params?: ListParams) =>
          http.get<Paginated<SkillChange>, Paginated<SkillChange>>(`app/v1/skills/${id}/changes/`, { params }),
      }),
      // Draft は Skill ごとに1つ（ページングしない）。変更では、編集を始めた時点の revision を送る
      drafts: (draftId?: string) => ({
        list: () => http.get<SkillDraft[], SkillDraft[]>(`app/v1/skills/${id}/drafts/`),
        get: () => http.get<SkillDraft, SkillDraft>(`app/v1/skills/${id}/drafts/${draftId}/`),
        create: (data: SkillDraftOpen) => http.post<SkillDraft, SkillDraft>(`app/v1/skills/${id}/drafts/`, data),
        patch: (data: Partial<SkillDraftWrite>) =>
          http.patch<SkillDraft, SkillDraft>(`app/v1/skills/${id}/drafts/${draftId}/`, data),
        delete: (revision: number) =>
          http.delete<undefined, undefined>(`app/v1/skills/${id}/drafts/${draftId}/`, { params: { revision } }),
        // 資料は key で識別し、ファイルは multipart で送る
        assets: (key?: string) => ({
          create: (data: SkillDraftAssetWrite) =>
            http.post<SkillDraftAsset, SkillDraftAsset>(
              `app/v1/skills/${id}/drafts/${draftId}/assets/`,
              toFormData(data),
              { timeout: UPLOAD_TIMEOUT },
            ),
          patch: (data: SkillDraftAssetUpdate) =>
            http.patch<SkillDraftAsset, SkillDraftAsset>(
              `app/v1/skills/${id}/drafts/${draftId}/assets/${key}/`,
              toFormData(data),
              { timeout: UPLOAD_TIMEOUT },
            ),
          // 検証の条件（CSVの必須列・キー列・型等）を変える。公開の権限が要る
          checks: (data: SkillDraftAssetChecks) =>
            http.put<SkillDraftAsset, SkillDraftAsset>(
              `app/v1/skills/${id}/drafts/${draftId}/assets/${key}/checks/`,
              data,
            ),
          delete: (revision: number) =>
            http.delete<undefined, undefined>(`app/v1/skills/${id}/drafts/${draftId}/assets/${key}/`, {
              params: { revision },
            }),
        }),
      }),
      // 資料の原本。ブラウザのリンクで開く（ダウンロードとして返る）
      fileUrl: (fileId: string) => `${config.public.apiEndpoint}app/v1/skills/${id}/files/${fileId}/download/`,
    }),
    runs: (id?: string) => ({
      ...readOnlyResource<Run, RunListParams>(http, 'app/v1/runs', id),
      cancel: (data: RunCancel) => http.post<Run, Run>(`app/v1/runs/${id}/cancel/`, data),
      // AppRunEventViewSet は list だけ
      events: () => ({
        list: (params?: ListParams) =>
          http.get<Paginated<RunStreamEvent>, Paginated<RunStreamEvent>>(`app/v1/runs/${id}/events/`, { params }),
      }),
      clarifications: (cid?: string) => ({
        ...readOnlyResource<Clarification, ClarificationListParams>(http, `app/v1/runs/${id}/clarifications`, cid),
        answer: (data: ClarificationAnswer) =>
          http.post<Clarification, Clarification>(`app/v1/runs/${id}/clarifications/${cid}/answer/`, data),
      }),
    }),
    manage: {
      users: (id?: string) => ({
        ...readOnlyResource<ManageUser, ManageUserListParams>(http, 'v1/users', id),
        // 作成の応答にだけ、招待のリンク（invitation）が入る
        create: (data: ManageUserWrite) => http.post<ManageUserCreated, ManageUserCreated>('v1/users/', data),
        patch: (data: Partial<ManageUserUpdate>) => http.patch<ManageUser, ManageUser>(`v1/users/${id}/`, data),
        invitation: () => http.post<InvitationLink, InvitationLink>(`v1/users/${id}/invitation/`),
      }),
      // 部門（Skill の部門の範囲に使う）と所属
      departments: (id?: string) => ({
        ...resource<Department, DepartmentWrite>(http, 'v1/departments', id),
        members: (memberId?: string) => ({
          ...readOnlyResource<DepartmentMembership, DepartmentMembershipListParams>(
            http,
            `v1/departments/${id}/members`,
            memberId,
          ),
          create: (data: DepartmentMembershipWrite) =>
            http.post<DepartmentMembership, DepartmentMembership>(`v1/departments/${id}/members/`, data),
          // 社員は変えられない（役割だけ）
          patch: (data: DepartmentMembershipUpdate) =>
            http.patch<DepartmentMembership, DepartmentMembership>(`v1/departments/${id}/members/${memberId}/`, data),
          delete: () => http.delete<undefined, undefined>(`v1/departments/${id}/members/${memberId}/`),
        }),
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
      runtimeProfiles: (id?: string) => ({
        ...resource<RuntimeProfile, RuntimeProfileWrite, RuntimeProfileListParams>(http, 'v1/runtime_profiles', id),
        makeDefault: () => http.post<RuntimeProfile, RuntimeProfile>(`v1/runtime_profiles/${id}/make_default/`),
        versions: (number?: number) =>
          readOnlyResource<RuntimeProfileVersion>(http, `v1/runtime_profiles/${id}/versions`, number?.toString()),
      }),
    },
  }
}

/** multipart で送る本文。null・undefined の項目は送らない（キーは snake_case に変換されないので、1語の項目だけに使う） */
const toFormData = (data: Record<string, string | number | Blob | null | undefined>) => {
  const form = new FormData()
  for (const [key, value] of Object.entries(data)) {
    if (value === null || value === undefined) continue
    form.append(key, value instanceof Blob ? value : String(value))
  }
  return form
}

let api: ReturnType<typeof createAPI> | null = null

export const useAPI = () => (api ??= createAPI())
