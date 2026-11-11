// バックエンドの run.models.RunStatus と同じ値。表示はサーバーの statusDisplay を使う
export const RunStatus = {
  QUEUED: 1,
  RUNNING: 2,
  WAITING_USER: 3,
  RETRY_WAIT: 4,
  CANCELLING: 5,
  WAITING_BACKGROUND: 6,
  COMPLETED: 10,
  FAILED: 20,
  CANCELLED: 30,
} as const satisfies Record<string, RunStatusEnum>

// バックエンドの run.models.RunKind と同じ値
export const RunKind = {
  WORK: 1,
  SKILL_EDIT: 2,
  EVALUATION: 3,
  // 修正チャットのタスクの編集（design.md §11.11）
  TASK_EDIT: 4,
} as const satisfies Record<string, RunKindEnum>

// バックエンドの run.models.RunTrigger と同じ値
export const RunTrigger = {
  CONVERSATION: 1,
  TASK: 2,
  EVALUATION: 3,
} as const satisfies Record<string, RunTriggerEnum>

// バックエンドの run.models.RunLaunch と同じ値（タスクのRunの起動のしかた）。表示はサーバーの launchDisplay を使う
export const RunLaunch = {
  MANUAL: 1,
  WEBHOOK: 2,
  CHAT: 3,
  FIX: 4,
} as const satisfies Record<string, RunLaunchEnum>

type T = (key: string, params?: Record<string, unknown>) => string

/** 状態の検索条件の選択肢（run.models.RunStatus の表示名と同じ。design.md §11.5）。一覧の表示はサーバーの statusDisplay を使う */
export const runStatusItems = (t: T) => [
  { value: RunStatus.QUEUED, title: t('順番待ち') },
  { value: RunStatus.RUNNING, title: t('作業中') },
  { value: RunStatus.WAITING_USER, title: t('確認待ち') },
  { value: RunStatus.WAITING_BACKGROUND, title: t('裏で作業を待っています') },
  { value: RunStatus.RETRY_WAIT, title: t('再試行待ち') },
  { value: RunStatus.CANCELLING, title: t('停止中') },
  { value: RunStatus.COMPLETED, title: t('完了') },
  { value: RunStatus.FAILED, title: t('失敗') },
  { value: RunStatus.CANCELLED, title: t('停止済み') },
]

/** 本番のタスクの実行の起動のしかたの検索条件の選択肢（修正チャットは本番の実行の一覧に出ないので除く） */
export const taskRunLaunchItems = (t: T) => [
  { value: RunLaunch.MANUAL, title: t('タスクの画面からの手動実行') },
  { value: RunLaunch.WEBHOOK, title: t('Webhook') },
  { value: RunLaunch.CHAT, title: t('チャットからの起動') },
]

// バックエンドの CANCELLABLE_STATUSES・ACTIVE_STATUSES と同じ
const CANCELLABLE: RunStatusEnum[] = [
  RunStatus.QUEUED,
  RunStatus.RUNNING,
  RunStatus.WAITING_USER,
  RunStatus.WAITING_BACKGROUND,
  RunStatus.RETRY_WAIT,
]
const ACTIVE: RunStatusEnum[] = [...CANCELLABLE, RunStatus.CANCELLING]

export const isCancellable = (run: Pick<Run, 'status'>) => CANCELLABLE.includes(run.status)
export const isActiveRun = (run: Pick<Run, 'status'>) => ACTIVE.includes(run.status)

/** 会話に作業の表示を出すか。1回の応答で終わった Run には、応答のほかに出すものがない */
export const hasWorkToShow = (run: Run) =>
  isActiveRun(run) ||
  run.status !== RunStatus.COMPLETED ||
  run.stepCount > 1 ||
  run.todos.length > 0 ||
  run.skills.length > 0 ||
  run.result != null ||
  run.kind === RunKind.SKILL_EDIT ||
  run.kind === RunKind.TASK_EDIT ||
  run.taskInfo != null

export const runStatusColor = (status: RunStatusEnum) => {
  switch (status) {
    case RunStatus.COMPLETED:
      return 'success'
    case RunStatus.FAILED:
      return 'error'
    case RunStatus.WAITING_USER:
    case RunStatus.RETRY_WAIT:
      return 'warning'
    case RunStatus.CANCELLING:
    case RunStatus.CANCELLED:
      return 'neutral'
    default:
      return 'primary'
  }
}

// バックエンドの run.models.RunEventKind のうち、画面で分岐に使うもの
export const RunEventKind = {
  USER_MESSAGE: 10,
  ASSISTANT_MESSAGE: 11,
  TOOL_RESULT: 21,
  JOB_FINISHED: 22,
  SUBAGENT_FINISHED: 23,
  COMPACTION: 32,
  MODEL_SWITCHED: 40,
  LLM_RETRY: 60,
} as const satisfies Record<string, RunEventKindEnum>

// 作業の記録に出す、そのほかの記録（run.event）。要求の中身・実行時の文脈等はモデル向けの記録なので出さない。
// 確認への回答は、Run の状態の変更の説明（run.status の comment）に出る
export const NOTABLE_EVENT_KINDS: RunEventKindEnum[] = [
  RunEventKind.JOB_FINISHED,
  RunEventKind.SUBAGENT_FINISHED,
  RunEventKind.COMPACTION,
  RunEventKind.MODEL_SWITCHED,
  RunEventKind.LLM_RETRY,
]

export const ClarificationKind = {
  QUESTION: 1,
  MODEL_SWITCH: 2,
  SKILL_EDIT: 3,
  // test_skill_draft で評価の完了を待つ。利用者は答えない（評価が終わるとサーバーが返す）
  EVALUATION: 4,
  // request_task_edit で、修正チャットのタスクの定義を直すかを確かめる
  TASK_EDIT: 5,
} as const satisfies Record<string, ClarificationKindEnum>

export const ClarificationStatus = {
  OPEN: 1,
  ANSWERED: 2,
  EXPIRED: 3,
  CANCELLED: 4,
} as const satisfies Record<string, ClarificationStatusEnum>

// バックエンドの run.models.ToolStatus と同じ値。表示はサーバーの statusDisplay を使う
export const ToolStatus = {
  REQUESTED: 1,
  RUNNING: 2,
  SUCCEEDED: 10,
  FAILED: 20,
  CANCELLED: 30,
  UNKNOWN: 40,
} as const satisfies Record<string, ToolStatusEnum>

// バックエンドの run.models.SubagentStatus と同じ値。表示はサーバーの statusDisplay を使う
export const SubagentStatus = {
  QUEUED: 1,
  RUNNING: 2,
  COMPLETED: 10,
  FAILED: 20,
  CANCELLED: 30,
} as const satisfies Record<string, SubagentStatusEnum>

export const isLiveSubagent = (subagent: Pick<Subagent, 'status'>) =>
  subagent.status === SubagentStatus.QUEUED || subagent.status === SubagentStatus.RUNNING

export const subagentStatusColor = (status: SubagentStatusEnum) => {
  switch (status) {
    case SubagentStatus.COMPLETED:
      return 'success'
    case SubagentStatus.FAILED:
      return 'error'
    case SubagentStatus.CANCELLED:
      return 'neutral'
    default:
      return 'primary'
  }
}

// サブエージェントを起動・停止する Tool。結果が届いたら、子の状態を読み直す
export const SUBAGENT_TOOLS = ['subagent', 'subagent_kill']

// Skill のスクリプトから Connector を呼べる Tool。Runner はこれらの終わりを待つ間に要求を処理する
// （harness.md §10.2）
export const SCRIPT_WAITING_TOOLS = ['shell', 'job_output']

// 作業の計画（todo_write）の項目の状態
export const TodoStatus = {
  PENDING: 'pending',
  IN_PROGRESS: 'in_progress',
  COMPLETED: 'completed',
} as const satisfies Record<string, Todo['status']>

/** Tool の呼出しを、作業の記録に出す短い文にする。知らない Tool は名前をそのまま出す */
export const toolLabel = (
  call: Pick<ToolCallInfo, 'name' | 'arguments'>,
  t: (key: string, params?: Record<string, unknown>) => string,
) => {
  const arg = (name: string) => {
    const value = call.arguments[name]
    return typeof value === 'string' ? value : ''
  }
  switch (call.name) {
    case 'todo_write':
      return t('作業の計画を更新')
    case 'ask_user':
      return t('確認の質問')
    case 'shell':
      // 画面に出す短い説明は、エージェントが description に書く
      return arg('description') || t('コマンドを実行')
    case 'read':
      return t('ファイルを読む：{path}', { path: arg('path') })
    case 'write':
      return t('ファイルを書く：{path}', { path: arg('path') })
    case 'edit':
      return t('ファイルを直す：{path}', { path: arg('path') })
    case 'glob':
      return t('ファイルを探す：{pattern}', { pattern: arg('pattern') })
    case 'grep':
      return t('内容を検索：{pattern}', { pattern: arg('pattern') })
    case 'job_output':
      return t('ジョブの出力を確認')
    case 'job_kill':
      return t('ジョブを停止')
    case 'skill':
      return t('Skillを読み込む：{name}', { name: arg('name') })
    case 'datasource_describe':
      return t('Connectorの表を確かめる：{connector}', { connector: arg('connector') })
    case 'datasource_query':
      return t('Connectorで読み取る：{connector}', { connector: arg('connector') })
    case 'submit_result':
      return t('業務結果を確定')
    case 'prepare_notification':
      return t('通知を準備')
    case 'request_skill_edit':
      return t('Skillの編集を提案')
    case 'get_skill':
      return t('Skillを読む')
    case 'create_skill':
      return t('Skillを作る：{name}', { name: arg('name') })
    case 'update_skill_draft':
      return t('SkillのDraftを変更')
    case 'stage_skill_asset':
      return t('Skillの資料を登録：{key}', { key: arg('key') })
    case 'remove_skill_asset':
      return t('Skillの資料を外す：{key}', { key: arg('key') })
    case 'publish_skill_draft':
      return t('Skillを公開')
    case 'rollback_skill':
      return t('Skillを切り戻す')
    case 'test_skill_draft':
      return t('SkillのDraftを評価')
    case 'request_task_edit':
      return t('タスクの修正を提案')
    case 'get_task':
      return t('タスクの定義を読む')
    case 'update_task':
      return t('タスクの新しい版を保存')
    case 'subagent':
      return t('サブエージェントに任せる：{description}', { description: arg('description') })
    case 'subagent_kill':
      return t('サブエージェントを止める')
    default:
      return call.name
  }
}
