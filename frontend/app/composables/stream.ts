import type { EffectScope, MaybeRefOrGetter } from 'vue'

// サーバーが送るイベントの種類（realtime.md §3）。EventSource は `event:` の名前ごとに受け取る
const EVENT_TYPES = ['run.status', 'run.message', 'run.delta', 'run.event', 'clarification.requested', 'notification']

// ブラウザが再接続をやめた後、こちらから接続し直すまでの間隔（ミリ秒）
const RECONNECT_DELAYS = [3000, 10000, 30000, 60000]

export type EventStreamOptions<T> = {
  onEvent: (event: T) => void
}

/**
 * SSE のストリームを読む（realtime.md §5）。path は apiEndpoint（/api/）からの相対パスで、null なら接続しない。
 * 切断時は EventSource が Last-Event-ID を付けて自動で再接続する。ブラウザが再接続をやめた場合（401・404・502 等）は、
 * ログイン状態を確かめてから、受け取った最後の id の続き（`after`）から接続し直す。
 */
export function useEventStream<T>(path: MaybeRefOrGetter<string | null>, { onEvent }: EventStreamOptions<T>) {
  const config = useRuntimeConfig()
  const api = useAPI()
  const connected = ref(false)
  let source: EventSource | null = null
  let lastEventId = ''
  let failures = 0
  let timer: ReturnType<typeof setTimeout> | null = null

  const close = () => {
    if (timer) clearTimeout(timer)
    timer = null
    source?.close()
    source = null
    connected.value = false
  }

  const handle = (e: MessageEvent<string>) => {
    if (e.lastEventId) lastEventId = e.lastEventId
    onEvent(camelProps<T>(JSON.parse(e.data)))
  }

  const open = (target: string) => {
    const url = new URL(`${config.public.apiEndpoint}${target}`, window.location.origin)
    if (lastEventId) url.searchParams.set('after', lastEventId)
    source = new EventSource(url)
    source.onopen = () => {
      connected.value = true
      failures = 0
    }
    source.onerror = async () => {
      connected.value = false
      if (source?.readyState !== EventSource.CLOSED) return // ブラウザが自動で再接続する
      close()
      // 未ログインなら 401 になり、API クライアントがログイン画面へ移動させる（前段の認証プロキシの切れ目なら再読み込み）
      try {
        await api.account.get()
      } catch {
        return
      }
      const delay = RECONNECT_DELAYS[Math.min(failures, RECONNECT_DELAYS.length - 1)]
      failures += 1
      timer = setTimeout(() => open(target), delay)
    }
    for (const type of EVENT_TYPES) source.addEventListener(type, handle)
  }

  watch(
    () => toValue(path),
    (target) => {
      close()
      lastEventId = ''
      failures = 0
      if (target) open(target)
    },
    { immediate: true },
  )
  onScopeDispose(close)

  return { connected, close }
}

type UserEventHandler = (event: UserStreamEvent) => void
const userEventHandlers = new Set<UserEventHandler>()
let userStreamScope: EffectScope | null = null

/**
 * ログインユーザー宛ての通知（app/v1/stream/）を受け取る。使う画面がいくつあっても、ストリームはタブに1本だけ開き、
 * 使う画面がなくなったら閉じる（1つのタブで開くストリームは2本まで。realtime.md §3）。
 */
export function useUserEvents(handler: UserEventHandler) {
  userEventHandlers.add(handler)
  if (!userStreamScope) {
    userStreamScope = effectScope(true)
    userStreamScope.run(() =>
      useEventStream<UserStreamEvent>('app/v1/stream/', {
        onEvent: (event) => userEventHandlers.forEach((h) => h(event)),
      }),
    )
  }
  onScopeDispose(() => {
    userEventHandlers.delete(handler)
    if (userEventHandlers.size === 0) {
      userStreamScope?.stop()
      userStreamScope = null
    }
  })
}
