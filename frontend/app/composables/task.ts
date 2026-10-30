import type { AxiosError } from 'axios'

// バックエンドの task.models.TaskScope と同じ値（Skill・Connectorの範囲と同じ）
export const TaskScope = {
  PERSONAL: 1,
  DEPARTMENT: 2,
  COMPANY: 3,
  SYSTEM: 4,
} as const satisfies Record<string, SkillScopeEnum>

// task.models.TaskInputType と同じ値
export const TaskInputType = {
  STRING: 'string',
  NUMBER: 'number',
  INTEGER: 'integer',
  BOOLEAN: 'boolean',
  DATE: 'date',
  CHOICE: 'choice',
  FILE: 'file',
} as const satisfies Record<string, TaskInputTypeEnum>

// delivery.models.NotifyEvent・NotifyContent と同じ値
export const NotifyEvent = {
  COMPLETED: 'run.completed',
  FAILED: 'run.failed',
  CANCELLED: 'run.cancelled',
} as const satisfies Record<string, NotifyEventEnum>

export const NotifyContent = {
  STATUS: 'status',
  RESULT: 'result',
  STATUS_AND_RESULT: 'status_and_result',
} as const satisfies Record<string, NotifyContentEnum>

// 1つのタスクに登録できる数（task.checks の MAX_TASK_SKILLS・MAX_TASK_CONNECTORS・MAX_INPUTS）
export const MAX_TASK_SKILLS = 5
export const MAX_TASK_CONNECTORS = 10
export const MAX_TASK_INPUTS = 30

type T = (key: string, params?: Record<string, unknown>) => string

/** 範囲の選択肢（検索条件と作成）。一覧の表示はサーバーの scopeDisplay を使う。システムは管理画面だけで使う */
export const taskScopeItems = (t: T) =>
  [
    { value: TaskScope.PERSONAL, title: t('個人'), description: t('自分の権限で動き、自分だけが管理します') },
    { value: TaskScope.DEPARTMENT, title: t('部門'), description: t('部門のタスクの管理者が管理します') },
    { value: TaskScope.COMPANY, title: t('全社'), description: t('全社のタスクの管理者が管理します') },
    {
      value: TaskScope.SYSTEM,
      title: t('システム'),
      description: t('システムのタスクの管理者が管理します（業務画面には出しません）'),
    },
  ] satisfies { value: SkillScopeEnum; title: string; description: string }[]

/**
 * 利用者が作れる範囲と、部門のタスクを作れる部門（authorization.md §7.3.1）。個人はいつでも、部門は管理できる部門と
 * 部門のタスクの管理のpermission、全社・システムはそれぞれのpermission。サーバーでも確かめ、なければ403
 */
export const useTaskCreatableScopes = () => {
  const account = useAccountStore()
  const { hasPermission } = usePermission()
  const departments = computed(() =>
    hasPermission(PermissionCode.MANAGE_DEPARTMENT_TASKS) ? (account.user?.manageableDepartments ?? []) : [],
  )
  const scopes = computed<SkillScopeEnum[]>(() => [
    TaskScope.PERSONAL,
    ...(departments.value.length ? [TaskScope.DEPARTMENT] : []),
    ...(hasPermission(PermissionCode.MANAGE_COMPANY_TASKS) ? [TaskScope.COMPANY] : []),
    ...(hasPermission(PermissionCode.MANAGE_SYSTEM_TASKS) ? [TaskScope.SYSTEM] : []),
  ])
  return { scopes, departments }
}

export const taskInputTypeItems = (t: T) =>
  [
    { value: TaskInputType.STRING, title: t('文字') },
    { value: TaskInputType.NUMBER, title: t('数値') },
    { value: TaskInputType.INTEGER, title: t('整数') },
    { value: TaskInputType.BOOLEAN, title: t('真偽') },
    { value: TaskInputType.DATE, title: t('日付') },
    { value: TaskInputType.CHOICE, title: t('選択肢') },
    { value: TaskInputType.FILE, title: t('ファイル') },
  ] satisfies { value: TaskInputTypeEnum; title: string }[]

export const notifyEventItems = (t: T) =>
  [
    { value: NotifyEvent.COMPLETED, title: t('完了') },
    { value: NotifyEvent.FAILED, title: t('失敗') },
    { value: NotifyEvent.CANCELLED, title: t('停止') },
  ] satisfies { value: NotifyEventEnum; title: string }[]

export const notifyContentItems = (t: T) =>
  [
    { value: NotifyContent.STATUS, title: t('状態だけ'), description: t('完了・失敗等の状態と、決まった失敗のコード') },
    { value: NotifyContent.RESULT, title: t('業務結果'), description: t('業務結果と、指定した成果物の取得用のURL') },
    {
      value: NotifyContent.STATUS_AND_RESULT,
      title: t('状態と業務結果'),
      description: t('状態に加えて、業務結果と成果物の取得用のURL'),
    },
  ] satisfies { value: NotifyContentEnum; title: string; description: string }[]

/** タスクの持ち主（個人は本人、部門は部門名、全社・システムは範囲の名前） */
export const taskOwnerLabel = (task: Task, t: T): string => {
  if (task.scope === TaskScope.DEPARTMENT) return task.departmentInfo?.name ?? ''
  if (task.scope === TaskScope.PERSONAL) return task.owner?.displayName || task.owner?.email || ''
  return task.scope === TaskScope.SYSTEM ? t('システム') : t('全社')
}

/** 候補のAPIに渡す範囲（タスクの範囲と部門） */
export const taskChoiceParams = (task: Pick<Task, 'scope' | 'departmentInfo'>): TaskChoiceParams => ({
  scope: task.scope,
  department: task.departmentInfo?.id ?? null,
  pageSize: 200,
})

/**
 * 手動実行の400の応答の inputs（項目の名前 → 誤り、か入力全体の誤りの文の並び）。キーは項目の名前なので、APIクライアント
 * が camelCase に変換する前の応答の本文から取り出す（foo_bar と foo__bar のように、変換すると同じになる名前があるため）
 */
export const taskInputErrors = (error: AxiosError): Record<string, unknown> | string[] | undefined => {
  if (error.response?.status !== 400) return undefined
  const data = error.response.data
  const inputs = data && typeof data === 'object' ? (data as Record<string, unknown>).inputs : undefined
  if (Array.isArray(inputs)) return inputs.map(String)
  return inputs && typeof inputs === 'object' ? (inputs as Record<string, unknown>) : undefined
}

/** 手動実行の入力の誤り（taskInputErrors() の辞書）から、項目の名前のまま誤りを引く */
export const inputErrorsOf = (errors: Record<string, unknown> | undefined, name: string): string[] | undefined => {
  const value = errors?.[name]
  if (Array.isArray(value)) return value.map(String)
  return typeof value === 'string' ? [value] : undefined
}

/** 入力の項目の編集中の行。選択肢は1行に1つの文で入力し、送るときに並びにする */
export type TaskInputRow = Omit<TaskInput, 'choices'> & { choicesText: string }

export const toInputRows = (inputs: readonly TaskInput[] | undefined): TaskInputRow[] =>
  (inputs ?? []).map(({ choices, ...input }) => ({ ...input, choicesText: (choices ?? []).join('\n') }))

export const fromInputRows = (rows: readonly TaskInputRow[]): TaskInput[] =>
  rows.map(({ choicesText, ...input }) => ({
    ...input,
    choices:
      input.type === TaskInputType.CHOICE
        ? choicesText
            .split('\n')
            .map((line) => line.trim())
            .filter(Boolean)
        : [],
    maxLength: input.type === TaskInputType.STRING ? (input.maxLength ?? null) : null,
  }))

export const emptyInputRow = (): TaskInputRow => ({
  name: '',
  label: '',
  type: TaskInputType.STRING,
  required: false,
  description: '',
  maxLength: null,
  choicesText: '',
})

/**
 * many=True の入力の項目の誤りから、行と項目の誤りを引く。DRF は行ごとの並び（[{}, { name: [...] }]）で返し、
 * 辞書（{ "1": { name: [...] } }）の形もある。項目全体の誤り（文の並び）は行の誤りではない
 */
export const rowErrorsOf = (errors: unknown, index: number, field: string): string[] | undefined => {
  if (!errors || typeof errors !== 'object') return undefined
  const row = Array.isArray(errors) ? errors[index] : (errors as Record<string, unknown>)[String(index)]
  const value = row && typeof row === 'object' ? (row as Record<string, unknown>)[field] : undefined
  return Array.isArray(value) ? value.map(String) : undefined
}
