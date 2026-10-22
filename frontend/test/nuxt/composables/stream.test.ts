import { mockNuxtImport } from '@nuxt/test-utils/runtime'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const { accountGet } = vi.hoisted(() => ({ accountGet: vi.fn() }))
mockNuxtImport('useAPI', () => () => ({ account: { get: accountGet } }))

class FakeEventSource {
  static CLOSED = 2
  static instances: FakeEventSource[] = []
  readyState = 1
  closed = false
  onopen: (() => void) | null = null
  onerror: (() => void) | null = null
  listeners = new Map<string, (e: MessageEvent<string>) => void>()
  constructor(public url: URL) {
    FakeEventSource.instances.push(this)
  }
  addEventListener(type: string, fn: (e: MessageEvent<string>) => void) {
    this.listeners.set(type, fn)
  }
  close() {
    this.closed = true
    this.readyState = FakeEventSource.CLOSED
  }
  emit(type: string, data: unknown, lastEventId = '') {
    this.listeners.get(type)?.(new MessageEvent(type, { data: JSON.stringify(data), lastEventId }))
  }
}

const latest = () => FakeEventSource.instances.at(-1)!

describe('useEventStream', () => {
  beforeEach(() => {
    FakeEventSource.instances = []
    vi.stubGlobal('EventSource', FakeEventSource)
    accountGet.mockReset().mockResolvedValue({})
  })
  afterEach(() => {
    vi.useRealTimers()
    vi.unstubAllGlobals()
  })

  it('イベントを camelCase にして渡し、path が変われば接続し直す', async () => {
    const path = ref<string | null>('app/v1/runs/r1/stream/')
    const events: unknown[] = []
    const scope = effectScope()
    scope.run(() => useEventStream(path, { onEvent: (e) => events.push(e) }))
    expect(latest().url.pathname).toBe('/api/app/v1/runs/r1/stream/')
    latest().emit('run.status', { type: 'run.status', status_display: '作業中' }, '3')
    expect(events).toEqual([{ type: 'run.status', statusDisplay: '作業中' }])

    const first = latest()
    path.value = null
    await nextTick()
    expect(first.closed).toBe(true)
    expect(FakeEventSource.instances).toHaveLength(1)
    scope.stop()
  })

  it('ブラウザが再接続をやめたら、ログイン状態を確かめてから、最後の id の続きから接続し直す', async () => {
    vi.useFakeTimers()
    const scope = effectScope()
    scope.run(() => useEventStream('app/v1/runs/r1/stream/', { onEvent: () => {} }))
    latest().emit('run.message', { type: 'run.message' }, '7')
    latest().readyState = FakeEventSource.CLOSED
    await latest().onerror!()
    expect(accountGet).toHaveBeenCalled()
    await vi.advanceTimersByTimeAsync(3000)
    expect(FakeEventSource.instances).toHaveLength(2)
    expect(latest().url.searchParams.get('after')).toBe('7')
    scope.stop()
    expect(latest().closed).toBe(true)
  })

  it('未ログインなら接続し直さない（API クライアントがログイン画面へ移す）', async () => {
    vi.useFakeTimers()
    accountGet.mockRejectedValue(new Error('401'))
    const scope = effectScope()
    scope.run(() => useEventStream('app/v1/stream/', { onEvent: () => {} }))
    latest().readyState = FakeEventSource.CLOSED
    await latest().onerror!()
    await vi.advanceTimersByTimeAsync(60000)
    expect(FakeEventSource.instances).toHaveLength(1)
    scope.stop()
  })

  it('ログイン状態を確かめる間に path が変わったら、古い path へ接続し直さない', async () => {
    vi.useFakeTimers()
    let resolve: (v: unknown) => void = () => {}
    accountGet.mockReturnValue(new Promise((r) => (resolve = r)))
    const path = ref<string | null>('app/v1/runs/a/stream/')
    const scope = effectScope()
    scope.run(() => useEventStream(path, { onEvent: () => {} }))
    const a = latest()
    a.readyState = FakeEventSource.CLOSED
    const pending = a.onerror!()
    path.value = 'app/v1/runs/b/stream/'
    await nextTick()
    resolve({})
    await pending
    await vi.advanceTimersByTimeAsync(60000)
    expect(FakeEventSource.instances.map((i) => i.url.pathname)).toEqual([
      '/api/app/v1/runs/a/stream/',
      '/api/app/v1/runs/b/stream/',
    ])
    scope.stop()
  })

  it('切断の後に接続し直したら onReconnect を呼ぶ（最初の接続では呼ばない）', async () => {
    const onReconnect = vi.fn()
    const scope = effectScope()
    scope.run(() => useEventStream('app/v1/stream/', { onEvent: () => {}, onReconnect }))
    latest().onopen!()
    expect(onReconnect).not.toHaveBeenCalled()
    // ブラウザが自動で再接続する場合（readyState は CONNECTING のまま）
    latest().readyState = 0
    await latest().onerror!()
    latest().onopen!()
    expect(onReconnect).toHaveBeenCalledTimes(1)
    scope.stop()
  })
})
