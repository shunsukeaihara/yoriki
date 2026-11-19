import { mockNuxtImport, mountSuspended } from '@nuxt/test-utils/runtime'
import { flushPromises } from '@vue/test-utils'
import { AxiosError, AxiosHeaders } from 'axios'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { TaskRunClarifications, TaskRunDeliveries } from '#components'

const { list, retry, deliveries, clarificationList } = vi.hoisted(() => {
  const list = vi.fn()
  const retry = vi.fn()
  return { list, retry, deliveries: vi.fn(() => ({ list, retry })), clarificationList: vi.fn() }
})

mockNuxtImport('useAPI', () => () => ({
  tasks: () => ({ deliveries }),
  runs: () => ({ clarifications: () => ({ list: clarificationList, answer: vi.fn() }) }),
}))
mockNuxtImport('useConfirmDialog', () => () => () => Promise.resolve(true))

const delivery = (id: string, status: DeliveryStatusEnum, statusDisplay: string, extra: Partial<Delivery> = {}) =>
  ({
    id,
    run: 'r1',
    eventType: 'run.completed',
    status,
    statusDisplay,
    destination: { id: 'd1', name: '経理のZapier', kind: 2, scope: 1, department: null, allowsSensitiveResults: false },
    destinationVersion: 1,
    payload: { event_id: 'e1', result: { VendorCode: 'A01' } },
    attemptCount: 1,
    nextAttempt: null,
    lastError: '',
    attempts: [],
    created: '2026-10-31T09:00:00+09:00',
    updated: '2026-10-31T09:00:00+09:00',
    delivered: null,
    ...extra,
  }) as Delivery

describe('TaskRunDeliveries', () => {
  beforeEach(() => {
    list.mockReset()
    retry.mockReset()
    deliveries.mockClear()
  })

  it('失敗した配信だけ「通知を再試行」を出し、送り直したら読み直す', async () => {
    list.mockResolvedValue({
      results: [
        delivery('a', DeliveryStatus.FAILED, '失敗', { lastError: 'HTTP 500' }),
        delivery('b', DeliveryStatus.DELIVERED, '受付確認済み', { delivered: '2026-10-31T09:01:00+09:00' }),
      ],
      next: null,
      previous: null,
    })
    const wrapper = await mountSuspended(TaskRunDeliveries, { props: { taskId: 't1', runId: 'r1' } })
    await flushPromises()
    expect(list).toHaveBeenCalledWith({ run: 'r1', pageSize: 100 })
    expect(wrapper.text()).toContain('HTTP 500')
    // 受付確認は、送り先での後の処理の完了を表さない
    expect(wrapper.text()).toContain('送り先での後の処理が終わったことは表しません')
    const buttons = wrapper.findAll('button').filter((b) => b.text().includes('通知を再試行'))
    expect(buttons).toHaveLength(1)

    retry.mockResolvedValue(delivery('a', DeliveryStatus.PENDING, '配信待ち'))
    await buttons[0]!.trigger('click')
    await flushPromises()
    expect(deliveries).toHaveBeenCalledWith('a')
    expect(retry).toHaveBeenCalled()
    expect(list).toHaveBeenCalledTimes(2)
    expect(wrapper.emitted('retried')).toHaveLength(1)
  })

  it('送り直しが受け付けられたら、読み直しに失敗しても受け付けた状態を出す', async () => {
    list.mockResolvedValueOnce({ results: [delivery('a', DeliveryStatus.FAILED, '失敗')], next: null, previous: null })
    const wrapper = await mountSuspended(TaskRunDeliveries, { props: { taskId: 't1', runId: 'r1' } })
    await flushPromises()
    retry.mockResolvedValue(delivery('a', DeliveryStatus.PENDING, '配信待ち'))
    list.mockRejectedValue(new Error('network'))
    await wrapper
      .findAll('button')
      .find((b) => b.text().includes('通知を再試行'))!
      .trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('配信待ち')
    expect(wrapper.text()).not.toContain('通知を再試行')
    expect(wrapper.emitted('retried')).toHaveLength(1)
  })

  it('送り直しを受け付けた後に届いた、前に始めた一覧の応答では表示を戻さない', async () => {
    list.mockResolvedValueOnce({ results: [delivery('a', DeliveryStatus.FAILED, '失敗')], next: null, previous: null })
    const wrapper = await mountSuspended(TaskRunDeliveries, {
      props: { taskId: 't1', runId: 'r1' },
    })
    await flushPromises()
    let resolveStale: (value: unknown) => void = () => {}
    list.mockReturnValueOnce(new Promise((r) => (resolveStale = r)))
    const stale = (wrapper.vm as unknown as { reload: () => Promise<void> }).reload()
    retry.mockResolvedValue(delivery('a', DeliveryStatus.PENDING, '配信待ち'))
    list.mockReturnValue(new Promise(() => {}))
    await wrapper
      .findAll('button')
      .find((b) => b.text().includes('通知を再試行'))!
      .trigger('click')
    await flushPromises()
    resolveStale({ results: [delivery('a', DeliveryStatus.FAILED, '失敗')], next: null, previous: null })
    await stale
    await flushPromises()
    expect(wrapper.text()).toContain('配信待ち')
    expect(wrapper.text()).not.toContain('通知を再試行')
  })

  it('配信待ち・配信中の間は、間をおいて読み直す', async () => {
    vi.useFakeTimers()
    try {
      list
        .mockResolvedValueOnce({
          results: [delivery('a', DeliveryStatus.SENDING, '配信中')],
          next: null,
          previous: null,
        })
        // 一度読めなくても、読み直しは続ける
        .mockRejectedValueOnce(new Error('network'))
        .mockResolvedValue({
          results: [delivery('a', DeliveryStatus.DELIVERED, '受付確認済み')],
          next: null,
          previous: null,
        })
      const wrapper = await mountSuspended(TaskRunDeliveries, { props: { taskId: 't1', runId: 'r1' } })
      await flushPromises()
      expect(list).toHaveBeenCalledTimes(1)
      await vi.advanceTimersByTimeAsync(5000)
      await flushPromises()
      expect(list).toHaveBeenCalledTimes(2)
      expect(wrapper.text()).toContain('配信中')
      await vi.advanceTimersByTimeAsync(5000)
      await flushPromises()
      expect(list).toHaveBeenCalledTimes(3)
      expect(wrapper.text()).toContain('受付確認済み')
      // 確定したら読み直さない
      await vi.advanceTimersByTimeAsync(20000)
      expect(list).toHaveBeenCalledTimes(3)
    } finally {
      vi.useRealTimers()
    }
  })

  it('送り直しの応答が返らなかったら、失敗と断定せずに状態を読み直す', async () => {
    list.mockResolvedValueOnce({ results: [delivery('a', DeliveryStatus.FAILED, '失敗')], next: null, previous: null })
    const wrapper = await mountSuspended(TaskRunDeliveries, { props: { taskId: 't1', runId: 'r1' } })
    await flushPromises()
    retry.mockRejectedValue(new AxiosError('Network Error'))
    list.mockResolvedValue({ results: [delivery('a', DeliveryStatus.PENDING, '配信待ち')], next: null, previous: null })
    await wrapper
      .findAll('button')
      .find((b) => b.text().includes('通知を再試行'))!
      .trigger('click')
    await flushPromises()
    expect(list).toHaveBeenCalledTimes(2)
    expect(wrapper.text()).toContain('配信待ち')
    expect(wrapper.emitted('retried')).toBeUndefined()
  })

  it('対話の出口への質問と回答の受付の配信にも、イベントの名前を出す', async () => {
    list.mockResolvedValue({
      results: [
        delivery('a', DeliveryStatus.DELIVERED, '受付確認済み', { eventType: 'run.waiting_user' }),
        delivery('b', DeliveryStatus.DELIVERED, '受付確認済み', { eventType: 'clarification.answered' }),
      ],
      next: null,
      previous: null,
    })
    const wrapper = await mountSuspended(TaskRunDeliveries, { props: { taskId: 't1', runId: 'r1' } })
    await flushPromises()
    expect(wrapper.text()).toContain('確認の通知')
    expect(wrapper.text()).toContain('回答の受付の通知')
    expect(wrapper.text()).not.toContain('run.waiting_user')
  })

  it('再試行待ちの配信は、次に送る日時を過ぎたら読み直す', async () => {
    vi.useFakeTimers()
    try {
      const next = new Date(Date.now() + 60_000).toISOString()
      list
        .mockResolvedValueOnce({
          results: [delivery('a', DeliveryStatus.RETRY_WAIT, '再試行待ち', { nextAttempt: next })],
          next: null,
          previous: null,
        })
        .mockResolvedValue({ results: [delivery('a', DeliveryStatus.FAILED, '失敗')], next: null, previous: null })
      await mountSuspended(TaskRunDeliveries, { props: { taskId: 't1', runId: 'r1' } })
      await flushPromises()
      await vi.advanceTimersByTimeAsync(30_000)
      expect(list).toHaveBeenCalledTimes(1)
      await vi.advanceTimersByTimeAsync(40_000)
      expect(list).toHaveBeenCalledTimes(2)
    } finally {
      vi.useRealTimers()
    }
  })
})

const question = (id: string, extra: Partial<Clarification> = {}): Clarification => ({
  id,
  run: 'r1',
  kind: ClarificationKind.QUESTION,
  status: ClarificationStatus.OPEN,
  target: null,
  questions: [
    {
      header: '',
      question: '支払日はいつにしますか',
      options: [
        { label: '月末', description: '' },
        { label: '翌月10日', description: '' },
      ],
      multiSelect: false,
    },
  ],
  answers: [],
  comment: '',
  answeredBy: null,
  answeredByName: '',
  answeredVia: '',
  answered: null,
  expires: null,
  created: '2026-11-01T09:00:00+09:00',
  ...extra,
})

describe('TaskRunClarifications', () => {
  // mockReset() は mock を返すので、波括弧で包む（返した関数は、vitest が後始末として呼ぶ）
  beforeEach(() => {
    clarificationList.mockReset()
  })

  it('回答待ちの確認には回答の欄を、回答済みの確認には誰がどこで答えたかを出す', async () => {
    clarificationList.mockResolvedValue({
      results: [
        question('q1', {
          status: ClarificationStatus.ANSWERED,
          answers: [{ selected: ['月末'], text: '' }],
          answeredByName: '佐藤',
          answeredVia: 'slack',
          answered: '2026-11-01T09:05:00+09:00',
        }),
        question('q2'),
      ],
      next: null,
      previous: null,
    })
    const wrapper = await mountSuspended(TaskRunClarifications, { props: { runId: 'r1' } })
    await flushPromises()
    expect(clarificationList).toHaveBeenCalledWith({ pageSize: 50 })
    expect(wrapper.text()).toContain('エージェントが回答を待っています')
    expect(wrapper.findAll('button').some((b) => b.text() === '回答する')).toBe(true)
    expect(wrapper.text()).toContain('佐藤さんがSlackで回答しました')
    expect(wrapper.text()).toContain('月末')
  })

  it('Webで答えた確認は、Webで回答したことを出す', async () => {
    clarificationList.mockResolvedValue({
      results: [
        question('q1', {
          status: ClarificationStatus.ANSWERED,
          answers: [{ selected: [], text: '来週で' }],
          answeredByName: '',
          answeredVia: 'web',
          answered: '2026-11-01T09:05:00+09:00',
        }),
      ],
      next: null,
      previous: null,
    })
    const wrapper = await mountSuspended(TaskRunClarifications, { props: { runId: 'r1' } })
    await flushPromises()
    expect(wrapper.text()).toContain('Webで回答しました')
    expect(wrapper.text()).toContain('来週で')
    expect(wrapper.text()).not.toContain('回答する')
  })

  it('確認を読めない人（404）には何も出さない', async () => {
    const notFound = new AxiosError('Not Found', '404', undefined, undefined, {
      status: 404,
      data: { detail: '見つかりません' },
      statusText: 'Not Found',
      headers: { 'content-type': 'application/json' },
      config: { headers: new AxiosHeaders() },
    })
    clarificationList.mockRejectedValue(notFound)
    const wrapper = await mountSuspended(TaskRunClarifications, { props: { runId: 'r1' } })
    await flushPromises()
    expect(clarificationList).toHaveBeenCalled()
    expect(wrapper.find('[data-testid="task-run-clarifications"]').exists()).toBe(false)
  })
})
