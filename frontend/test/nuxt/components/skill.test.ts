import { mockNuxtImport, mountSuspended } from '@nuxt/test-utils/runtime'
import { AxiosError, AxiosHeaders } from 'axios'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { SkillDraftAssetList, SkillDraftForm } from '#components'

const { patch, assetDelete } = vi.hoisted(() => ({ patch: vi.fn(), assetDelete: vi.fn() }))

mockNuxtImport('useAPI', () => () => ({
  skills: () => ({
    drafts: () => ({ patch, assets: () => ({ delete: assetDelete }) }),
    fileUrl: (id: string) => `/api/files/${id}`,
  }),
}))
mockNuxtImport('useConfirmDialog', () => () => () => Promise.resolve(true))

const user = { id: 'u1', email: 'taro@example.co.jp', displayName: '太郎' }
const file: ResourceVersion = {
  id: 'f1',
  resource: 'r1',
  number: 1,
  name: 'vendor-master.csv',
  contentType: 'text/csv',
  size: 39,
  sha256: 'a'.repeat(64),
  createdBy: 'u1',
  created: '2026-10-24T00:00:00Z',
}
const draft: SkillDraft = {
  id: 'd1',
  baseVersion: 1,
  revision: 3,
  description: '請求書を確認する',
  instructions: '1. 読む',
  tools: ['shell'],
  requires: [],
  inputSchema: null,
  outputSchema: { type: 'object' },
  assets: [
    { key: 'vendor-master', role: 2, roleDisplay: 'マスターデータ', description: '', fileInfo: file, draftRevision: 3 },
  ],
  updatedByInfo: user,
  created: '2026-10-24T00:00:00Z',
  updated: '2026-10-24T00:00:00Z',
}

type Form = InstanceType<typeof SkillDraftForm> & {
  form: { description: string; tools: string[]; outputSchema: string }
}

describe('SkillDraftForm', () => {
  beforeEach(() => {
    patch
      .mockReset()
      .mockImplementation((data: Partial<SkillDraftWrite>) =>
        Promise.resolve({ ...draft, ...data, revision: (data.revision ?? 0) + 1 }),
      )
  })

  it('編集を始めた時点の revision を送り、公開の権限がなければ Tool を送らない', async () => {
    const wrapper = await mountSuspended(SkillDraftForm, {
      props: { modelValue: draft, skillId: 's1', canChangeTools: false },
    })
    const vm = wrapper.vm as unknown as Form
    expect(vm.canPost).toBe(false) // 変更がなければ保存しない
    vm.form.description = '請求書と発注書を確認する'
    await nextTick()
    expect(vm.dirty).toBe(true)
    await vm.onSubmit()
    const data = patch.mock.calls[0]![0]
    expect(data).toMatchObject({
      revision: 3,
      description: '請求書と発注書を確認する',
      outputSchema: { type: 'object' },
    })
    expect(data).not.toHaveProperty('tools')
    // 保存した後は、応答の中身で入れ直す
    expect(vm.dirty).toBe(false)
  })

  it('公開の権限があれば Tool も送る', async () => {
    const wrapper = await mountSuspended(SkillDraftForm, {
      props: { modelValue: draft, skillId: 's1', canChangeTools: true },
    })
    const vm = wrapper.vm as unknown as Form
    vm.form.tools = ['shell', 'read']
    await vm.onSubmit()
    expect(patch.mock.calls[0]![0]).toMatchObject({ tools: ['shell', 'read'] })
  })

  it('Schema が JSON として読めなければ保存できない', async () => {
    const wrapper = await mountSuspended(SkillDraftForm, {
      props: { modelValue: draft, skillId: 's1', canChangeTools: true },
    })
    const vm = wrapper.vm as unknown as Form
    vm.form.outputSchema = '{"type": '
    await nextTick()
    expect(vm.canPost).toBe(false)
    expect(wrapper.text()).toContain('JSONとして読めません')
  })

  it('資料の操作で revision が増えても、入力中の文は残す', async () => {
    const wrapper = await mountSuspended(SkillDraftForm, {
      props: { modelValue: draft, skillId: 's1', canChangeTools: false },
    })
    const vm = wrapper.vm as unknown as Form
    vm.form.description = '入力中'
    await wrapper.setProps({ modelValue: { ...draft, revision: 4 } })
    expect(vm.form.description).toBe('入力中')
    await vm.onSubmit()
    expect(patch.mock.calls[0]![0]).toMatchObject({ revision: 4 })
  })

  it('409 は failed で知らせる', async () => {
    const conflict = new AxiosError('conflict', '409', undefined, undefined, {
      status: 409,
      statusText: 'Conflict',
      data: { detail: '他の更新と競合しました。' },
      headers: { 'content-type': 'application/json' },
      config: { headers: new AxiosHeaders() },
    })
    patch.mockReset().mockRejectedValue(conflict)
    const wrapper = await mountSuspended(SkillDraftForm, {
      props: { modelValue: draft, skillId: 's1', canChangeTools: false },
    })
    const vm = wrapper.vm as unknown as Form
    vm.form.description = '変更'
    await vm.onSubmit()
    expect(wrapper.emitted('failed')![0]![0]).toBe(conflict)
  })
})

describe('SkillDraftAssetList', () => {
  beforeEach(() => {
    assetDelete.mockReset().mockResolvedValue(undefined)
  })

  it('資料を外すと、revision を1つ進めて一覧から除く', async () => {
    const wrapper = await mountSuspended(SkillDraftAssetList, { props: { modelValue: draft, skillId: 's1' } })
    expect(wrapper.text()).toContain('vendor-master.csv')
    await wrapper.find('button[aria-label="外す"]').trigger('click')
    await flushPromises()
    expect(assetDelete).toHaveBeenCalledWith(3)
    const updated = wrapper.emitted('update:modelValue')![0]![0] as SkillDraft
    expect(updated.revision).toBe(4)
    expect(updated.assets).toEqual([])
  })
})

const flushPromises = () => new Promise((resolve) => setTimeout(resolve))
