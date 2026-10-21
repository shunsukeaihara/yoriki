// バックエンドの run.models.RunStatus と同じ値。表示はサーバーの statusDisplay を使う
export const RunStatus = {
  QUEUED: 1,
  RUNNING: 2,
  WAITING_USER: 3,
  RETRY_WAIT: 4,
  CANCELLING: 5,
  COMPLETED: 10,
  FAILED: 20,
  CANCELLED: 30,
} as const satisfies Record<string, RunStatusEnum>

// バックエンドの CANCELLABLE_STATUSES・ACTIVE_STATUSES と同じ
const CANCELLABLE: RunStatusEnum[] = [RunStatus.QUEUED, RunStatus.RUNNING, RunStatus.WAITING_USER, RunStatus.RETRY_WAIT]
const ACTIVE: RunStatusEnum[] = [...CANCELLABLE, RunStatus.CANCELLING]

export const isCancellable = (run: Pick<Run, 'status'>) => CANCELLABLE.includes(run.status)
export const isActiveRun = (run: Pick<Run, 'status'>) => ACTIVE.includes(run.status)

/** 会話に作業の表示を出すか。1回の応答で終わった Run には、応答のほかに出すものがない */
export const hasWorkToShow = (run: Run) =>
  isActiveRun(run) || run.status !== RunStatus.COMPLETED || run.stepCount > 1 || run.todos.length > 0

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
  COMPACTION: 32,
  MODEL_SWITCHED: 40,
  LLM_RETRY: 60,
} as const satisfies Record<string, RunEventKindEnum>

// 作業の記録に出す、そのほかの記録（run.event）。要求の中身・実行時の文脈等はモデル向けの記録なので出さない。
// 確認への回答は、Run の状態の変更の説明（run.status の comment）に出る
export const NOTABLE_EVENT_KINDS: RunEventKindEnum[] = [
  RunEventKind.COMPACTION,
  RunEventKind.MODEL_SWITCHED,
  RunEventKind.LLM_RETRY,
]

export const ClarificationKind = {
  QUESTION: 1,
  MODEL_SWITCH: 2,
} as const satisfies Record<string, ClarificationKindEnum>

export const ClarificationStatus = {
  OPEN: 1,
  ANSWERED: 2,
  EXPIRED: 3,
  CANCELLED: 4,
} as const satisfies Record<string, ClarificationStatusEnum>

// 作業の計画（todo_write）の項目の状態
export const TodoStatus = {
  PENDING: 'pending',
  IN_PROGRESS: 'in_progress',
  COMPLETED: 'completed',
} as const satisfies Record<string, Todo['status']>

/** Tool の呼出しを、作業の記録に出す短い文にする。知らない Tool は名前をそのまま出す */
export const toolLabel = (name: string, t: (key: string) => string) => {
  switch (name) {
    case 'todo_write':
      return t('作業の計画を更新')
    case 'ask_user':
      return t('確認の質問')
    default:
      return name
  }
}
