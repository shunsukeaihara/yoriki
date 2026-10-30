import { mockNuxtImport, mountSuspended } from '@nuxt/test-utils/runtime'
import { flushPromises } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { TaskRunDeliveries } from '#components'

const { list, retry, deliveries } = vi.hoisted(() => {
  const list = vi.fn()
  const retry = vi.fn()
  return { list, retry, deliveries: vi.fn(() => ({ list, retry })) }
})

mockNuxtImport('useAPI', () => () => ({ tasks: () => ({ deliveries }) }))
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
    expect(list).toHaveBeenCalledWith({ run: 'r1', pageSize: 50 })
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
})
