import { mockNuxtImport, mountSuspended } from '@nuxt/test-utils/runtime'
import { flushPromises } from '@vue/test-utils'
import { setActivePinia, createPinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { NotificationBell } from '#components'

const { unreadCount, list, read, readAll, handlers } = vi.hoisted(() => ({
  unreadCount: vi.fn(),
  list: vi.fn(),
  read: vi.fn(),
  readAll: vi.fn(),
  handlers: [] as ((event: UserStreamEvent) => void)[],
}))

mockNuxtImport('useAPI', () => () => ({ notifications: { unreadCount, list, read, readAll } }))
mockNuxtImport('useUserEvents', () => (handler: (event: UserStreamEvent) => void) => handlers.push(handler))

const notification = (id: string, readAt: string | null = null): AppNotification => ({
  id,
  eventType: 'run.completed',
  eventDisplay: '完了',
  task: 't1',
  taskName: '請求書チェック',
  run: 'r1',
  read: readAt,
  created: '2026-10-29T09:00:00+09:00',
})

describe('NotificationBell', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    handlers.length = 0
    unreadCount.mockReset().mockResolvedValue({ unread: 2 })
    list
      .mockReset()
      .mockResolvedValue({ results: [notification('n1'), notification('n2')], next: null, previous: null })
    read.mockReset().mockImplementation(async (id: string) => notification(id, '2026-10-29T10:00:00+09:00'))
    readAll.mockReset().mockResolvedValue({ unread: 0 })
  })

  it('未読の数を出し、通知が届いたら数え直す', async () => {
    const wrapper = await mountSuspended(NotificationBell)
    await flushPromises()
    expect(wrapper.text()).toContain('2')
    unreadCount.mockResolvedValue({ unread: 3 })
    // 会話の Run の状態の変更では数え直さない
    handlers.forEach((h) => h({ type: 'run.status', run: 'r', conversation: 'c', status: 2, sequence: 1 }))
    await flushPromises()
    expect(unreadCount).toHaveBeenCalledTimes(1)
    handlers.forEach((h) => h({ type: 'notification' }))
    await flushPromises()
    expect(unreadCount).toHaveBeenCalledTimes(2)
    expect(wrapper.text()).toContain('3')
  })

  it('開いた通知を既読にし、未読の数を減らす', async () => {
    await mountSuspended(NotificationBell)
    const store = useNotificationStore()
    await store.reload()
    unreadCount.mockResolvedValue({ unread: 1 })
    await store.markRead(store.items[0]!)
    expect(read).toHaveBeenCalledWith('n1')
    expect(store.unread).toBe(1)
    expect(store.items[0]!.read).not.toBeNull()
    // 既読のものは送らない
    await store.markRead(store.items[0]!)
    expect(read).toHaveBeenCalledTimes(1)
    unreadCount.mockResolvedValue({ unread: 0 })
    await store.markAllRead()
    expect(readAll).toHaveBeenCalled()
    expect(store.unread).toBe(0)
  })

  it('ログインユーザーが変わったら、前の人の通知を消す', async () => {
    const account = useAccountStore()
    account.user = { id: 'u1' } as MyUser
    const store = useNotificationStore()
    await store.reload()
    expect(store.items).toHaveLength(2)
    account.user = null
    await nextTick()
    expect(store.items).toEqual([])
    expect(store.unread).toBe(0)
    // 読んでいる途中に利用者が変わったら、前の人への応答は反映しない
    account.user = { id: 'u1' } as MyUser
    let resolve: (value: unknown) => void = () => {}
    list.mockReturnValueOnce(new Promise((r) => (resolve = r)))
    const pending = store.reload()
    account.user = { id: 'u2' } as MyUser
    await nextTick()
    resolve({ results: [notification('n1')], next: null, previous: null })
    await pending
    expect(store.items).toEqual([])
  })
})
