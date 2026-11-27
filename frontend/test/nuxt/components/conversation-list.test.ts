import { mockNuxtImport, mountSuspended } from '@nuxt/test-utils/runtime'
import { flushPromises } from '@vue/test-utils'
import { setActivePinia, createPinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { ConversationList } from '#components'

const { list, get, handlers } = vi.hoisted(() => ({
  list: vi.fn(),
  get: vi.fn(),
  handlers: [] as ((event: UserStreamEvent) => void)[],
}))

mockNuxtImport('useAPI', () => () => ({ conversations: () => ({ list, get }) }))
mockNuxtImport('useUserEvents', () => (handler: (event: UserStreamEvent) => void) => handlers.push(handler))

const statusEvent = (conversation: string, listed: boolean): UserStreamEvent => ({
  type: 'run.status',
  run: 'r1',
  conversation,
  listed,
  status: 2,
  sequence: 1,
})

describe('ConversationList', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    handlers.length = 0
    list.mockReset().mockResolvedValue({ results: [], next: null, previous: null })
    get.mockReset().mockRejectedValue(new Error('not mocked'))
  })

  it('Run の状態の通知で、一覧に出る会話だけを読み直す', async () => {
    await mountSuspended(ConversationList)
    await flushPromises()
    // 評価・タスクの Run の会話は一覧に出ないので、取りに行かない（取りに行くと404になる）
    handlers.forEach((h) => h(statusEvent('task-conversation', false)))
    await flushPromises()
    expect(get).not.toHaveBeenCalled()
    get.mockResolvedValue({ id: 'c1', title: '請求書の確認', archived: null, lastPosted: '2026-11-28T09:00:00+09:00' })
    handlers.forEach((h) => h(statusEvent('c1', true)))
    await flushPromises()
    expect(get).toHaveBeenCalledTimes(1)
  })
})
