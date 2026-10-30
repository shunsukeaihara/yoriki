import { mockNuxtImport, mountSuspended } from '@nuxt/test-utils/runtime'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { DestinationForm, TriggerBindingForm, TriggerBindingPreview } from '#components'

const { create, patch, triggerPatch, preview, emptyList } = vi.hoisted(() => ({
  create: vi.fn(),
  patch: vi.fn(),
  triggerPatch: vi.fn(),
  preview: vi.fn(),
  emptyList: vi.fn().mockResolvedValue({ results: [], next: null, previous: null }),
}))

mockNuxtImport('useAPI', () => () => ({
  manage: {
    destinations: () => ({ create, patch, departments: emptyList }),
    triggerBindings: () => ({ patch: triggerPatch, preview, tasks: emptyList }),
  },
}))

const user = { id: 'u1', email: 'u1@example.com', displayName: 'U1' }

const destination: Destination = {
  id: 'd1',
  kind: DestinationKind.ZAPIER,
  kindDisplay: 'Zapier',
  scope: DestinationScope.DEPARTMENT,
  scopeDisplay: '部門',
  department: 'dep1',
  departmentInfo: { id: 'dep1', name: '経理部' },
  name: '経理のZap',
  suspended: false,
  version: 2,
  urlDisplay: 'hooks.zapier.com …def/',
  allowsSensitiveResults: false,
  secretRotated: null,
  createdBy: user,
  created: '2026-10-31T00:00:00Z',
  updated: '2026-10-31T00:00:00Z',
}

const binding: TriggerBinding = {
  id: 'b1',
  kind: 1,
  task: 't1',
  taskInfo: {
    id: 't1',
    name: '請求書チェック',
    scope: TaskScope.COMPANY,
    scopeDisplay: '全社',
    department: null,
    suspended: false,
    inputs: [
      { name: 'vendor', label: '取引先', description: '', type: TaskInputType.STRING, required: true },
      { name: 'invoice', label: '請求書', description: '', type: TaskInputType.FILE, required: false },
    ],
  },
  name: '経費SaaS',
  suspended: false,
  version: 3,
  eventIdPath: '$.event.id',
  inputs: [
    { name: 'vendor', path: '$.data.vendor_name' },
    // タスクから消した項目の取り出し（受付時には使わない）
    { name: 'amount', path: '$.data.total' },
  ],
  maxPayloadBytes: 2 * 1024 * 1024,
  url: '/api/hooks/v1/tasks/t1/',
  keyPrefix: 'yrk_abcd',
  keyRotated: null,
  createdBy: user,
  created: '2026-10-31T00:00:00Z',
  updated: '2026-10-31T00:00:00Z',
}

type Exposed = { onSubmit: () => Promise<void>; canPost: boolean; reset: () => void }

describe('DestinationForm', () => {
  beforeEach(() => {
    create.mockReset().mockResolvedValue({ ...destination, signingSecret: 'whsec_x' })
    patch.mockReset().mockResolvedValue(destination)
  })

  type Form = Exposed & {
    form: { name: string; kind: number; scope: number; department: string | null; url: string }
  }

  it('作成では種類・範囲・部門とURLを送り、URLと（部門の範囲なら）部門を入れるまで保存できない', async () => {
    const wrapper = await mountSuspended(DestinationForm, { props: { modelValue: null } })
    const vm = wrapper.vm as unknown as Form
    vm.form.name = '経理のWebhook'
    await nextTick()
    expect(vm.canPost).toBe(false)
    vm.form.url = ' https://example.com/hook '
    vm.form.department = 'dep1'
    await nextTick()
    expect(vm.canPost).toBe(true)
    await vm.onSubmit()
    expect(create.mock.calls[0]![0]).toMatchObject({
      kind: DestinationKind.WEBHOOK,
      scope: DestinationScope.DEPARTMENT,
      department: 'dep1',
      url: 'https://example.com/hook',
    })
  })

  it('画面の通知はURLを送らず、全社の範囲では部門を送らない', async () => {
    const wrapper = await mountSuspended(DestinationForm, { props: { modelValue: null } })
    const vm = wrapper.vm as unknown as Form
    Object.assign(vm.form, {
      name: '全社の通知',
      kind: DestinationKind.IN_APP,
      scope: DestinationScope.COMPANY,
      department: 'dep1',
      url: 'https://example.com/hook',
    })
    await nextTick()
    expect(vm.canPost).toBe(true)
    await vm.onSubmit()
    const data = create.mock.calls[0]![0]
    expect(data).toMatchObject({ kind: DestinationKind.IN_APP, scope: DestinationScope.COMPANY, department: null })
    expect(data.url).toBeUndefined()
  })

  it('更新では版を送り、空のURLと変えていない名前は送らない。種類・範囲は送らない', async () => {
    const wrapper = await mountSuspended(DestinationForm, { props: { modelValue: destination } })
    expect(wrapper.text()).toContain('hooks.zapier.com …def/')
    expect(wrapper.text()).toContain('経理部')
    const vm = wrapper.vm as unknown as Form
    await vm.onSubmit()
    const data = patch.mock.calls[0]![0]
    expect(data).toMatchObject({ version: 2, allowsSensitiveResults: false })
    for (const key of ['url', 'name', 'kind', 'scope', 'department']) expect(data[key]).toBeUndefined()

    vm.form.name = '経理のZap（本番）'
    vm.form.url = 'https://hooks.zapier.com/hooks/catch/1/new/'
    await vm.onSubmit()
    expect(patch.mock.calls[1]![0]).toMatchObject({
      name: '経理のZap（本番）',
      url: 'https://hooks.zapier.com/hooks/catch/1/new/',
    })
  })

  it('停止等で出口が変わっても、未保存の入力と編集を始めた時点の版を残す', async () => {
    const wrapper = await mountSuspended(DestinationForm, { props: { modelValue: destination } })
    const vm = wrapper.vm as unknown as Form & { form: { version: number } }
    vm.form.url = 'https://hooks.zapier.com/hooks/catch/1/new/'
    await wrapper.setProps({ modelValue: { ...destination, suspended: true, version: 3 } })
    expect(vm.form).toMatchObject({ url: 'https://hooks.zapier.com/hooks/catch/1/new/', version: 2 })
    vm.reset()
    expect(vm.form).toMatchObject({ url: '', version: 3 })
  })
})

describe('TriggerBindingForm', () => {
  beforeEach(() => {
    triggerPatch.mockReset().mockResolvedValue(binding)
  })

  type Form = Exposed & {
    form: { name: string; eventIdPath: string; inputs: TriggerInputRow[]; maxPayloadKib: number | null }
    settings: { eventIdPath: string; inputs: TriggerInput[] }
  }

  it('書きかけの行を除いた取り出しと上限（バイト）を送り、名前は変えたときだけ送る', async () => {
    const wrapper = await mountSuspended(TriggerBindingForm, { props: { modelValue: binding } })
    const vm = wrapper.vm as unknown as Form
    expect(vm.form.maxPayloadKib).toBe(2048)
    // タスクから消した項目は、受付時に使わないことを示す
    expect(wrapper.text()).toContain('タスクにない項目です。受付時には使いません')
    vm.form.inputs.push({ name: null, path: '$.x' })
    vm.form.eventIdPath = ' $.id '
    await nextTick()
    expect(vm.settings).toEqual({
      eventIdPath: '$.id',
      inputs: [
        { name: 'vendor', path: '$.data.vendor_name' },
        { name: 'amount', path: '$.data.total' },
      ],
    })
    await vm.onSubmit()
    const data = triggerPatch.mock.calls[0]![0]
    expect(data).toMatchObject({ version: 3, eventIdPath: '$.id', maxPayloadBytes: 2 * 1024 * 1024 })
    expect(data.inputs).toHaveLength(2)
    expect(data.name).toBeUndefined()

    vm.form.name = '経費SaaS（本番）'
    await vm.onSubmit()
    expect(triggerPatch.mock.calls[1]![0]).toMatchObject({ name: '経費SaaS（本番）' })
  })
})

describe('TriggerBindingPreview', () => {
  const settings = { eventIdPath: '$.id', inputs: [{ name: 'vendor', path: '$.vendor' }] }

  beforeEach(() => {
    preview.mockReset()
  })

  it('編集中の受け取り方で試し、取り出した値・誤り・ファイルの項目を出す。プロンプトは返されたときだけ', async () => {
    preview.mockResolvedValue({
      formatError: '',
      eventId: 'evt-1',
      eventIdError: '',
      values: [{ name: 'vendor', value: 'ACME' }],
      inputErrors: [{ name: 'amount', message: '整数を入力してください' }],
      fileInputs: ['invoice'],
      canViewPrompt: false,
      prompt: null,
    })
    const wrapper = await mountSuspended(TriggerBindingPreview, { props: { bindingId: 'b1', settings } })
    const vm = wrapper.vm as unknown as { onSubmit: () => Promise<void> }
    await wrapper.find('textarea').setValue('{"id": "evt-1", "vendor": "ACME"}')
    await vm.onSubmit()
    await nextTick()
    expect(preview).toHaveBeenCalledWith({ sample: '{"id": "evt-1", "vendor": "ACME"}', ...settings })
    const text = wrapper.find('[data-testid="trigger-preview-result"]').text()
    expect(text).toContain('受付で400として断ります')
    expect(text).toContain('整数を入力してください')
    expect(text).toContain('evt-1')
    expect(text).toContain('ACME')
    expect(text).toContain('invoice')
    expect(text).toContain('タスクの依頼文は表示しません')

    // 試した後に受け取り方を変えたら、結果が古いことを示す
    await wrapper.setProps({ settings: { ...settings, eventIdPath: '$.event.id' } })
    expect(wrapper.text()).toContain('もう一度試してください')
  })

  it('読めないpayloadは、その理由だけを出す', async () => {
    preview.mockResolvedValue({
      formatError: 'JSONとして読めません',
      eventId: null,
      eventIdError: '',
      values: [],
      inputErrors: [],
      fileInputs: [],
      canViewPrompt: true,
      prompt: null,
    })
    const wrapper = await mountSuspended(TriggerBindingPreview, { props: { bindingId: 'b1', settings } })
    await wrapper.find('textarea').setValue('{')
    await (wrapper.vm as unknown as { onSubmit: () => Promise<void> }).onSubmit()
    await nextTick()
    const text = wrapper.find('[data-testid="trigger-preview-result"]').text()
    expect(text).toContain('JSONとして読めません')
    expect(text).not.toContain('取り出した値')
  })
})
