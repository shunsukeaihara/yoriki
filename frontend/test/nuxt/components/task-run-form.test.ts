import { mockNuxtImport, mountSuspended } from '@nuxt/test-utils/runtime'
import { flushPromises } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { TaskRunForm } from '#components'

const { fixChat } = vi.hoisted(() => ({ fixChat: vi.fn() }))

mockNuxtImport('useAPI', () => () => ({ tasks: () => ({ fixChat }) }))

const task = {
  id: 't1',
  name: '請求書チェック',
  version: 2,
  inputs: [
    { name: 'vendor', label: '取引先', type: TaskInputType.STRING, required: true },
    { name: 'invoice', label: '請求書', type: TaskInputType.FILE, required: true },
  ],
} as unknown as TaskDetail

describe('TaskRunForm（修正チャット）', () => {
  beforeEach(() => {
    fixChat.mockReset()
    fixChat.mockResolvedValue({ id: 'r2', conversation: 'c1' })
  })

  it('元の実行のファイルを出し、パスのまま元の実行と一緒に送る', async () => {
    const wrapper = await mountSuspended(TaskRunForm, {
      props: {
        task,
        fix: true,
        fromRunId: 'r1',
        clientRequestId: 'req-1',
        initialValues: { vendor: 'A商事', invoice: '/workspace/input/請求書.pdf' },
      },
    })
    expect(wrapper.text()).toContain('請求書.pdf')
    expect(wrapper.text()).toContain('前の実行のファイル')
    await (wrapper.vm as unknown as { onSubmit: () => Promise<void> }).onSubmit()
    await flushPromises()
    expect(fixChat).toHaveBeenCalledWith(
      2,
      'req-1',
      { vendor: 'A商事', invoice: '/workspace/input/請求書.pdf' },
      undefined,
      'r1',
    )
  })

  it('外したファイルは付け直せる', async () => {
    const wrapper = await mountSuspended(TaskRunForm, {
      props: { task, fix: true, fromRunId: 'r1', initialValues: { invoice: '/workspace/input/請求書.pdf' } },
    })
    await wrapper.find('button[aria-label="外す"]').trigger('click')
    expect(wrapper.text()).not.toContain('前の実行のファイル')
    expect(wrapper.text()).toContain('ファイルをドロップ')
  })
})
