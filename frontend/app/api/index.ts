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
    // 会話で使える Connector（入力欄の `@` の候補と、Skill で宣言する Connector の候補）
    connectors: {
      list: (params?: ListParams) =>
        http.get<Paginated<AppConnector>, Paginated<AppConnector>>('app/v1/connectors/', { params }),
      get: (id: string) => http.get<AppConnectorDetail, AppConnectorDetail>(`app/v1/connectors/${id}/`),
    },
    // 本人の画面の通知（通知欄）。中身はタスクの名前と Run の状態だけ
    notifications: {
      list: (params?: NotificationListParams) =>
        http.get<Paginated<AppNotification>, Paginated<AppNotification>>('app/v1/notifications/', { params }),
      unreadCount: () => http.get<NotificationCount, NotificationCount>('app/v1/notifications/unread_count/'),
      read: (id: string) => http.post<AppNotification, AppNotification>(`app/v1/notifications/${id}/read/`),
      readAll: () => http.post<NotificationCount, NotificationCount>('app/v1/notifications/read_all/'),
    },
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
      // 評価ケース（ページングしない）。添付は multipart で送る（design.md §3.7.1）
      evaluationCases: (caseId?: string) => ({
        list: () => http.get<SkillEvaluationCase[], SkillEvaluationCase[]>(`app/v1/skills/${id}/evaluation_cases/`),
        create: (data: SkillEvaluationCaseWrite) =>
          http.post<SkillEvaluationCase, SkillEvaluationCase>(
            `app/v1/skills/${id}/evaluation_cases/`,
            evaluationCaseForm(data),
            { timeout: UPLOAD_TIMEOUT },
          ),
        patch: (data: Partial<SkillEvaluationCaseWrite>) =>
          http.patch<SkillEvaluationCase, SkillEvaluationCase>(
            `app/v1/skills/${id}/evaluation_cases/${caseId}/`,
            evaluationCaseForm(data),
            { timeout: UPLOAD_TIMEOUT },
          ),
        delete: () => http.delete<undefined, undefined>(`app/v1/skills/${id}/evaluation_cases/${caseId}/`),
      }),
      // Draftの評価。一覧は結果を含まない
      evaluations: (evaluationId?: string) => ({
        list: (params?: ListParams) =>
          http.get<Paginated<MinimumSkillEvaluation>, Paginated<MinimumSkillEvaluation>>(
            `app/v1/skills/${id}/evaluations/`,
            { params },
          ),
        get: () => http.get<SkillEvaluation, SkillEvaluation>(`app/v1/skills/${id}/evaluations/${evaluationId}/`),
        create: (data: SkillEvaluationCreate) =>
          http.post<SkillEvaluation, SkillEvaluation>(`app/v1/skills/${id}/evaluations/`, data),
        cancel: () =>
          http.post<SkillEvaluation, SkillEvaluation>(`app/v1/skills/${id}/evaluations/${evaluationId}/cancel/`),
      }),
      // 資料の原本。ブラウザのリンクで開く（ダウンロードとして返る）
      fileUrl: (fileId: string) => `${config.public.apiEndpoint}app/v1/skills/${id}/files/${fileId}/download/`,
    }),
    // タスク（design.md §4.1）。一覧は管理できるものだけ。保存のたびに版が増え、更新では編集を始めた時点の version を送る
    tasks: (id?: string) => ({
      list: (params?: TaskListParams) => http.get<Paginated<Task>, Paginated<Task>>('app/v1/tasks/', { params }),
      get: () => http.get<TaskDetail, TaskDetail>(`app/v1/tasks/${id}/`),
      create: (data: TaskCreate) => http.post<TaskDetail, TaskDetail>('app/v1/tasks/', data),
      patch: (data: TaskUpdate) => http.patch<TaskDetail, TaskDetail>(`app/v1/tasks/${id}/`, data),
      delete: () => http.delete<undefined, undefined>(`app/v1/tasks/${id}/`),
      suspend: () => http.post<TaskDetail, TaskDetail>(`app/v1/tasks/${id}/suspend/`),
      resume: () => http.post<TaskDetail, TaskDetail>(`app/v1/tasks/${id}/resume/`),
      // 手動実行。いつも multipart で送り、inputs はJSONの文字列にする（項目の名前のキーを変換しないため）。
      // 型がファイルの項目は、項目の名前のフィールドでファイルを付ける。version は画面で確かめた版（違えば409）で、
      // 入力の項目の名前と重ならないようクエリで送る
      run: (version: number, inputs: TaskRunInputs) => {
        const form = new FormData()
        const values: Record<string, unknown> = {}
        for (const [name, value] of Object.entries(inputs)) {
          if (value instanceof File) form.append(name, value)
          else if (value !== null && value !== '') values[name] = value
        }
        form.append('inputs', JSON.stringify(values))
        return http.post<Run, Run>(`app/v1/tasks/${id}/run/`, form, { timeout: UPLOAD_TIMEOUT, params: { version } })
      },
      // 版は版番号で識別する
      revisions: () => ({
        list: (params?: ListParams) =>
          http.get<Paginated<TaskRevision>, Paginated<TaskRevision>>(`app/v1/tasks/${id}/revisions/`, { params }),
      }),
      // Runの履歴（結果を見られるもの）
      runs: () => ({
        list: (params?: TaskRunListParams) =>
          http.get<Paginated<Run>, Paginated<Run>>(`app/v1/tasks/${id}/runs/`, { params }),
      }),
      // 編集画面で選ぶ候補（その範囲のタスクに登録できるもの。authorization.md §7.2）
      skillChoices: (params: TaskChoiceParams) =>
        http.get<Paginated<TaskSkillChoice>, Paginated<TaskSkillChoice>>('app/v1/tasks/skill_choices/', { params }),
      connectorChoices: (params: TaskChoiceParams) =>
        http.get<Paginated<AppConnector>, Paginated<AppConnector>>('app/v1/tasks/connector_choices/', { params }),
    }),
    // タスクの編集画面で選ぶ出口の候補（タスクを管理できる範囲の、停止していないもの）
    destinations: {
      list: (params: TaskChoiceParams) =>
        http.get<Paginated<DestinationChoice>, Paginated<DestinationChoice>>('app/v1/destinations/', { params }),
    },
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
      // 部門（部門のSkill・Connector等の所有の単位）と所属
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
          // 所属は変更せず、消して作り直す
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
      // 社内DBの接続先と資格情報。確認と表の一覧は接続先のDBにつなぐので、接続のタイムアウトまで待つ（waitSeconds）
      databaseConnections: (id?: string) => ({
        ...resource<DatabaseConnection, DatabaseConnectionWrite, DatabaseConnectionListParams>(
          http,
          'v1/database_connections',
          id,
        ),
        // 更新では種別を送れない
        update: (data: DatabaseConnectionUpdate) =>
          http.put<DatabaseConnection, DatabaseConnection>(`v1/database_connections/${id}/`, data),
        patch: (data: Partial<DatabaseConnectionUpdate>) =>
          http.patch<DatabaseConnection, DatabaseConnection>(`v1/database_connections/${id}/`, data),
        check: (waitSeconds: number) =>
          http.post<ConnectorCheck, ConnectorCheck>(`v1/database_connections/${id}/check/`, null, waitFor(waitSeconds)),
        // 接続先のアカウントが読める表・ビューの一覧（Connectorで公開する表を選ぶ）
        tables: (waitSeconds: number) =>
          http.get<ConnectorTables, ConnectorTables>(`v1/database_connections/${id}/tables/`, waitFor(waitSeconds)),
      }),
      connectors: (id?: string) => ({
        ...resource<Connector, ConnectorWrite, ConnectorListParams>(http, 'v1/connectors', id),
        // 更新では使うDB接続を送れない
        update: (data: ConnectorUpdate) => http.put<Connector, Connector>(`v1/connectors/${id}/`, data),
        patch: (data: Partial<ConnectorUpdate>) => http.patch<Connector, Connector>(`v1/connectors/${id}/`, data),
        // 作成と一覧の絞り込みで選ぶ接続先と部門（部門は所属を問わず有効なすべて）。接続先・部門の管理の権限がなくても選べる
        connections: (params?: ListParams) =>
          http.get<Paginated<MinimumDatabaseConnection>, Paginated<MinimumDatabaseConnection>>(
            'v1/connectors/connections/',
            { params },
          ),
        departments: (params?: ListParams) =>
          http.get<Paginated<MinimumDepartment>, Paginated<MinimumDepartment>>('v1/connectors/departments/', {
            params,
          }),
        // 公開する表を選ぶための、使う接続先の表・ビューの一覧
        tables: (waitSeconds: number) =>
          http.get<ConnectorTables, ConnectorTables>(`v1/connectors/${id}/tables/`, waitFor(waitSeconds)),
        // 公開している表・ビューの説明（エージェントに見せる内容）と試し実行。接続先のDBにつなぐので、
        // 接続と実行のタイムアウト（waitSeconds。connectorWaitSeconds()）まで待つ
        describe: (waitSeconds: number) =>
          http.get<ConnectorTables, ConnectorTables>(`v1/connectors/${id}/describe/`, waitFor(waitSeconds)),
        query: (data: ConnectorQuery, waitSeconds: number) =>
          http.post<ConnectorQueryResult, ConnectorQueryResult>(
            `v1/connectors/${id}/query/`,
            data,
            waitFor(waitSeconds),
          ),
        versions: (number?: number) =>
          readOnlyResource<ConnectorVersion>(http, `v1/connectors/${id}/versions`, number?.toString()),
      }),
    },
  }
}

/** サーバーが外へつなぐ要求の待ち時間。サーバー側のタイムアウトより少し長く待ち、通常のタイムアウトより短くしない */
const waitFor = (seconds: number) => ({ timeout: Math.max(API_TIMEOUT, (seconds + 15) * 1000) })

/** multipart で送る本文。null・undefined の項目は送らない（キーは snake_case に変換されないので、1語の項目だけに使う） */
const toFormData = (data: Record<string, string | number | Blob | null | undefined>) => {
  const form = new FormData()
  for (const [key, value] of Object.entries(data)) {
    if (value === null || value === undefined) continue
    form.append(key, value instanceof Blob ? value : String(value))
  }
  return form
}

/** 評価ケースの multipart の本文（キーは snake_case）。省いた項目は送らない（部分更新） */
const evaluationCaseForm = (data: Partial<SkillEvaluationCaseWrite>) => {
  const form = new FormData()
  const text: [string, string | undefined][] = [
    ['name', data.name],
    ['prompt', data.prompt],
    ['criteria', data.criteria],
  ]
  for (const [key, value] of text) if (value !== undefined) form.append(key, value)
  if (data.required !== undefined) form.append('required', String(data.required))
  if (data.expectedResult !== undefined) form.append('expected_result', JSON.stringify(data.expectedResult))
  for (const file of data.addFiles ?? []) form.append('add_files', file)
  for (const fileId of data.removeFiles ?? []) form.append('remove_files', fileId)
  return form
}

let api: ReturnType<typeof createAPI> | null = null

export const useAPI = () => (api ??= createAPI())
