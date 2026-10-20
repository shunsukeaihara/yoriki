import { mockNuxtImport, mountSuspended } from '@nuxt/test-utils/runtime'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { LlmConnectionForm } from '#components'

const { create, patch } = vi.hoisted(() => ({ create: vi.fn(), patch: vi.fn() }))

mockNuxtImport('useAPI', () => () => ({
  manage: { llmConnections: () => ({ create, patch, check: vi.fn() }) },
}))

const local: LLMConnection = {
  id: 'c1',
  name: '社内vLLM',
  kind: ProviderKind.LOCAL,
  kindDisplay: 'ローカルLLM',
  baseUrl: 'http://vllm.internal:8000/v1',
  hasApiKey: true,
  apiKeyHint: 'abcd',
  timeoutSeconds: 120,
  maxConcurrency: 4,
  requestsPerMinute: null,
  isActive: true,
  createdBy: 'u1',
  created: '2026-10-20T00:00:00Z',
  updated: '2026-10-20T00:00:00Z',
}

type Form = InstanceType<typeof LlmConnectionForm>

describe('LlmConnectionForm', () => {
  beforeEach(() => {
    create.mockReset().mockResolvedValue(local)
    patch.mockReset().mockResolvedValue(local)
  })

  it('作成では種別を送り、クラウドサービスには base URL を送らない', async () => {
    const wrapper = await mountSuspended(LlmConnectionForm, { props: { modelValue: null } })
    const inputs = wrapper.findAll('input[type="text"], input[type="password"]')
    await inputs[0]!.setValue('OpenAI')
    await wrapper.find('input[type="password"]').setValue('sk-test')
    await (wrapper.vm as unknown as Form).onSubmit()
    expect(create).toHaveBeenCalledWith(
      expect.objectContaining({ name: 'OpenAI', kind: ProviderKind.OPENAI, baseUrl: '', apiKey: 'sk-test' }),
    )
  })

  it('更新では、API key を入力しなければ送らない（登録済みの値を変えない）', async () => {
    const wrapper = await mountSuspended(LlmConnectionForm, { props: { modelValue: local } })
    await (wrapper.vm as unknown as Form).onSubmit()
    const data = patch.mock.calls[0]![0]
    expect(data).not.toHaveProperty('apiKey')
    // 種別と base URL は作成後に変えられないので送らない
    expect(data).not.toHaveProperty('kind')
    expect(data).not.toHaveProperty('baseUrl')
    expect(wrapper.text()).toContain('登録済み（末尾 abcd）')
  })

  it('更新で API key を入力したら差し替え、削除を選んだら空文字を送る', async () => {
    const wrapper = await mountSuspended(LlmConnectionForm, { props: { modelValue: local } })
    await wrapper.find('input[type="password"]').setValue('new-key')
    await (wrapper.vm as unknown as Form).onSubmit()
    expect(patch.mock.calls[0]![0]).toMatchObject({ apiKey: 'new-key' })

    await wrapper.find('button[role="checkbox"]').trigger('click')
    await (wrapper.vm as unknown as Form).onSubmit()
    expect(patch.mock.calls[1]![0]).toMatchObject({ apiKey: '' })
  })
})
