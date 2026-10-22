import { AxiosError } from 'axios'
import type { Ref } from 'vue'

// バックエンドの conversation.models と同じ値
export const MessageRole = {
  USER: 1,
  AGENT: 2,
  SYSTEM: 3,
} as const satisfies Record<string, MessageRoleEnum>

export const InstructionKind = {
  CURRENT: 1,
  NEXT: 2,
} as const satisfies Record<string, InstructionKindEnum>

export const InstructionState = {
  RECEIVED: 1,
  APPLIED: 2,
  STARTED: 3,
  DISCARDED: 9,
} as const satisfies Record<string, InstructionStateEnum>

// メッセージの送り方（MessageSerializer.mode）
export const MessageMode = {
  // 実行中なら今の作業への追加指示、確認待ちなら回答、それ以外は新しい依頼
  AUTO: 'auto',
  // 実行中なら、今の作業が終わった後に始める次の依頼
  NEXT: 'next',
} as const satisfies Record<string, NonNullable<MessageWrite['mode']>>

/** 先行の作業の後に始めるのを待っている次の依頼 */
export const isWaitingNextRequest = (message: Message) =>
  message.instruction?.kind === InstructionKind.NEXT && message.instruction.state === InstructionState.RECEIVED

/** 会話の画面に並べる項目。メッセージと、Run ごとの作業の表示 */
export type TimelineItem =
  { kind: 'message'; id: string; message: Message } | { kind: 'run'; id: string; runId: string }

/**
 * メッセージ（古い順）の間に、Run ごとの作業の表示を1つずつ差し込む。
 * エージェントの応答があればその直前に、なければ（実行中・失敗・停止）その Run の利用者のメッセージの最後の後に置く。
 * 次の依頼として待っているメッセージは、まだ始まっていないので、作業の表示の位置を決めるのに使わない。
 */
export const buildTimeline = (messages: Message[]): TimelineItem[] => {
  const replied = new Set<string>()
  const lastUserIndex = new Map<string, number>()
  messages.forEach((m, index) => {
    if (!m.run) return
    if (m.role === MessageRole.AGENT) replied.add(m.run)
    else if (!isWaitingNextRequest(m)) lastUserIndex.set(m.run, index)
  })
  const placed = new Set<string>()
  const items: TimelineItem[] = []
  const placeRun = (runId: string) => {
    if (placed.has(runId)) return
    placed.add(runId)
    items.push({ kind: 'run', id: `run-${runId}`, runId })
  }
  messages.forEach((m, index) => {
    if (m.run && m.role === MessageRole.AGENT) placeRun(m.run)
    items.push({ kind: 'message', id: m.id, message: m })
    if (m.run && !replied.has(m.run) && lastUserIndex.get(m.run) === index) placeRun(m.run)
  })
  return items
}

// 一度に読むメッセージの数
const MESSAGE_PAGE_SIZE = 50

/** 会話のメッセージを新しい順のページで読み、古い順に並べて持つ。古いページは loadOlder() で読む */
export function useConversationMessages(conversationId: string) {
  const api = useAPI()
  const items = ref([]) as Ref<Message[]>
  const older = ref<string | null>(null)
  const loaded = ref(false)
  const loadingOlder = ref(false)

  const merge = (list: Message[]) => {
    const byId = new Map(items.value.map((m) => [m.id, m]))
    for (const m of list) byId.set(m.id, m)
    items.value = [...byId.values()].sort((a, b) => a.created.localeCompare(b.created))
  }

  // 読み直しが重なったら、最後に始めたものの結果だけを使う（古い応答で、追加指示の反映状態を戻さないように）
  let refreshing = 0

  /** 最新のページを読み直す（新しいメッセージと、追加指示の反映状態の変化を取り込む） */
  const refresh = async () => {
    const request = ++refreshing
    const page = await api.conversations(conversationId).messages().list({ pageSize: MESSAGE_PAGE_SIZE })
    if (request !== refreshing) return
    merge(page.results)
    if (!loaded.value) older.value = page.next
    loaded.value = true
  }

  const loadOlder = async () => {
    if (!older.value) return
    loadingOlder.value = true
    try {
      const page = await api.http.get<Paginated<Message>, Paginated<Message>>(older.value)
      merge(page.results)
      older.value = page.next
    } finally {
      loadingOlder.value = false
    }
  }

  return {
    items,
    loaded,
    loadingOlder,
    hasOlder: computed(() => Boolean(older.value)),
    refresh,
    loadOlder,
    add: (message: Message) => merge([message]),
  }
}

export type RunLiveHandlers = {
  onEvent?: (event: RunStreamEvent) => void
  /** 切断の後に接続し直したとき（切れていた間の LLM の出力の差分は送り直されない） */
  onReconnect?: () => void
}

/**
 * 実行中の Run のイベントをストリームで受け取る。最初の接続で、それまでのイベントもすべて届く。
 * LLM の出力の差分（run.delta）は、確定したエージェントの応答が届くまで delta に貯める。
 */
export function useRunLive(runId: Ref<string | null>, { onEvent, onReconnect }: RunLiveHandlers = {}) {
  const events = ref([]) as Ref<RunStreamEvent[]>
  const delta = ref('')

  // ストリームの接続し直しより先に、前の Run の表示を消す
  watch(runId, () => {
    events.value = []
    delta.value = ''
  })

  useEventStream<RunStreamEvent>(() => (runId.value ? `app/v1/runs/${runId.value}/stream/` : null), {
    onReconnect,
    onEvent: (event) => {
      // 前の Run のストリームの残り（切り替えの途中に届いたもの）は使わない
      if (event.run !== runId.value) return
      if (event.type === 'run.delta') {
        delta.value += event.text
        return
      }
      const last = events.value.at(-1)
      if (last && 'sequence' in last && last.sequence >= event.sequence) return // 再接続で重なった分
      if (event.type === 'run.message' && event.kind === RunEventKind.ASSISTANT_MESSAGE) delta.value = ''
      if (event.type === 'run.status' && !isActiveRun(event)) delta.value = ''
      events.value.push(event)
      onEvent?.(event)
    },
  })

  return { events, delta }
}

// サイドバーに一度に読む会話の数
const CONVERSATION_PAGE_SIZE = 30

/**
 * サイドバーの会話の一覧（新しく投稿した順）。会話の画面で作成・変更・削除したら upsert()・remove() で反映し、
 * ログインユーザー宛ての通知（Run の状態の変更）を受けたら、その会話を読み直す。
 */
export const useConversationListStore = defineStore('conversationList', () => {
  const items = ref<Conversation[]>([])
  const next = ref<string | null>(null)
  const loading = ref(false)

  const reload = async () => {
    loading.value = true
    try {
      const page = await useAPI().conversations().list({ pageSize: CONVERSATION_PAGE_SIZE })
      items.value = page.results
      next.value = page.next
    } finally {
      loading.value = false
    }
  }

  const loadMore = async () => {
    if (!next.value) return
    loading.value = true
    try {
      const page = await useAPI().http.get<Paginated<Conversation>, Paginated<Conversation>>(next.value)
      const known = new Set(items.value.map((c) => c.id))
      items.value = items.value.concat(page.results.filter((c) => !known.has(c.id)))
      next.value = page.next
    } finally {
      loading.value = false
    }
  }

  /** 会話を一覧に反映する。新しく投稿した会話は先頭へ移す */
  const upsert = (conversation: Conversation) => {
    const rest = items.value.filter((c) => c.id !== conversation.id)
    const index = items.value.findIndex((c) => c.id === conversation.id)
    const moved = index !== 0 && conversation.lastPosted !== items.value[index]?.lastPosted
    if (index < 0 || moved) items.value = [conversation, ...rest]
    else items.value = items.value.map((c) => (c.id === conversation.id ? conversation : c))
  }

  const remove = (id: string) => {
    items.value = items.value.filter((c) => c.id !== id)
  }

  /**
   * 1件を読み直す（通知を受けたときの裏の更新なので、失敗しても画面にエラーを出さない）。
   * 削除済み・他人の会話（404）なら一覧から外す
   */
  const refreshOne = async (id: string) => {
    try {
      upsert(await useAPI().conversations(id).get())
    } catch (err) {
      if (err instanceof AxiosError && err.response?.status === 404) remove(id)
    }
  }

  return { items, loading, hasMore: computed(() => Boolean(next.value)), reload, loadMore, upsert, remove, refreshOne }
})
