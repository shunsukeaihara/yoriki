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
      { name: 'vendor', label: '取引先', type: TaskInputType.STRING, required: true },
      { name: 'invoice', label: '請求書', type: TaskInputType.FILE, required: false },
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

  it('競合の後の読み直しでは、入力し直せない送り先のURLだけを残す', async () => {
    const wrapper = await mountSuspended(DestinationForm, { props: { modelValue: destination } })
    const vm = wrapper.vm as unknown as Omit<Form, 'reset'> & {
      form: { version: number }
      reset: (options?: { keepUrl?: boolean }) => void
    }
    vm.form.name = '経理のZap（本番）'
    vm.form.url = 'https://hooks.zapier.com/hooks/catch/1/new/'
    await wrapper.setProps({ modelValue: { ...destination, version: 3, name: '別の人の名前' } })
    vm.reset({ keepUrl: true })
    expect(vm.form).toMatchObject({
      name: '別の人の名前',
      url: 'https://hooks.zapier.com/hooks/catch/1/new/',
      version: 3,
    })
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
    // 取り出しと上限は、変えていなければ入れない（サーバーは今の版の値を使う）
    expect(vm.settings).toEqual({ eventIdPath: '$.id', inputs: undefined, maxPayloadBytes: undefined })
    await vm.onSubmit()
    const data = triggerPatch.mock.calls[0]![0]
    expect(data).toMatchObject({ version: 3, eventIdPath: '$.id' })
    expect(data.maxPayloadBytes).toBeUndefined()
    // 取り出しを変えていなければ送らない（タスクから消した項目の取り出しが残っていても、ほかの設定を保存できる）
    expect(data.inputs).toBeUndefined()
    expect(data.name).toBeUndefined()

    vm.form.inputs.splice(1, 1)
    await vm.onSubmit()
    expect(triggerPatch.mock.calls.at(-1)![0].inputs).toEqual([{ name: 'vendor', path: '$.data.vendor_name' }])

    vm.form.name = '経費SaaS（本番）'
    await vm.onSubmit()
    expect(triggerPatch.mock.calls.at(-1)![0]).toMatchObject({ name: '経費SaaS（本番）' })
  })
})

describe('TriggerBindingForm（上限）', () => {
  beforeEach(() => {
    triggerPatch.mockReset().mockResolvedValue(binding)
  })

  it('KiBで割り切れない上限は、変えなければ丸めて送らず、変えたらKiBから送る', async () => {
    const wrapper = await mountSuspended(TriggerBindingForm, {
      props: { modelValue: { ...binding, maxPayloadBytes: 1_500_000 } },
    })
    const vm = wrapper.vm as unknown as Exposed & {
      form: { maxPayloadKib: number | null }
      settings: { maxPayloadBytes?: number }
    }
    await vm.onSubmit()
    expect(triggerPatch.mock.calls[0]![0].maxPayloadBytes).toBeUndefined()
    vm.form.maxPayloadKib = 512
    await nextTick()
    expect(vm.settings.maxPayloadBytes).toBe(512 * 1024)
    await vm.onSubmit()
    expect(triggerPatch.mock.calls[1]![0].maxPayloadBytes).toBe(512 * 1024)
  })
})

describe('TriggerBindingPreview', () => {
  const settings = { eventIdPath: '$.id', inputs: [{ name: 'vendor', path: '$.vendor' }] }

  beforeEach(() => {
    preview.mockReset()
  })

  it('編集中の受け取り方で試し、取り出した値・誤り・ファイルの項目を出す。プロンプトは返されたときだけ', async () => {
    preview.mockResolvedValue({
      outcome: TriggerPreviewOutcome.REFUSED,
      formatError: '',
      eventId: 'evt-1',
      eventIdError: '',
      values: [{ name: 'vendor', value: 'ACME' }],
      inputErrors: [{ name: 'amount', message: '整数を入力してください' }],
      fileInputs: ['invoice'],
      requiredFileInputs: [],
      runProblems: [],
      duplicate: false,
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
    expect(text).toContain('受付で断ります')
    expect(text).toContain('整数を入力してください')
    expect(text).toContain('evt-1')
    expect(text).toContain('ACME')
    expect(text).toContain('invoice')
    // 断られる要求では、欠けた値で埋めたプロンプトを見せない
    expect(text).toContain('受付で断るので、プロンプトは作りません')

    // 試した後に受け取り方を変えたら、結果が古いことを示す
    await wrapper.setProps({ settings: { ...settings, eventIdPath: '$.event.id' } })
    expect(wrapper.text()).toContain('もう一度試してください')
  })

  it('受け付けられる要求では、プロンプトを返されたときだけ出し、返されなければ権限がないことを示す', async () => {
    const ok = {
      outcome: TriggerPreviewOutcome.ACCEPTED,
      requiredFileInputs: [],
      runProblems: [],
      duplicate: false,
      formatError: '',
      eventId: null,
      eventIdError: '',
      values: [{ name: 'vendor', value: 'ACME' }],
      inputErrors: [],
      fileInputs: [],
    }
    preview.mockResolvedValueOnce({ ...ok, canViewPrompt: false, prompt: null })
    preview.mockResolvedValueOnce({
      ...ok,
      duplicate: true,
      canViewPrompt: true,
      prompt: 'ACME の請求書を確認してください。',
    })
    const wrapper = await mountSuspended(TriggerBindingPreview, { props: { bindingId: 'b1', settings } })
    const vm = wrapper.vm as unknown as { onSubmit: () => Promise<void> }
    await wrapper.find('textarea').setValue('{"vendor": "ACME"}')
    await vm.onSubmit()
    await nextTick()
    expect(wrapper.text()).toContain('この要求は受け付けられます')
    expect(wrapper.text()).toContain('タスクの依頼文は表示しません')
    expect(wrapper.text()).toContain('重複を判定せず')
    await vm.onSubmit()
    await nextTick()
    expect(wrapper.text()).toContain('ACME の請求書を確認してください。')
    // 前に受け付けた要求と同じなら、新しいRunを作らないことを示す
    expect(wrapper.text()).toContain('送ると前のRunを返し、新しいRunは作りません')
  })

  it('イベントIDだけの誤りでも断ると示し、必須のファイルの項目があれば確かめられないと示す', async () => {
    const base = {
      formatError: '',
      eventId: null,
      values: [{ name: 'vendor', value: 'ACME' }],
      inputErrors: [],
      fileInputs: ['invoice'],
      canViewPrompt: true,
    }
    preview.mockResolvedValueOnce({
      ...base,
      outcome: TriggerPreviewOutcome.REFUSED,
      eventIdError: 'イベントIDは文字か整数で送ってください',
      requiredFileInputs: [],
      runProblems: [],
      duplicate: false,
      prompt: null,
    })
    preview.mockResolvedValueOnce({
      ...base,
      outcome: TriggerPreviewOutcome.UNCONFIRMED,
      eventIdError: '',
      requiredFileInputs: ['invoice'],
      runProblems: [],
      duplicate: false,
      prompt: 'ACME を確認してください。',
    })
    const wrapper = await mountSuspended(TriggerBindingPreview, { props: { bindingId: 'b1', settings } })
    const vm = wrapper.vm as unknown as { onSubmit: () => Promise<void> }
    await wrapper.find('textarea').setValue('{"vendor": "ACME"}')
    await vm.onSubmit()
    await nextTick()
    expect(wrapper.text()).toContain('受付で断ります')
    expect(wrapper.text()).toContain('イベントID：イベントIDは文字か整数で送ってください')
    expect(wrapper.text()).not.toContain('この要求は受け付けられます')
    await vm.onSubmit()
    await nextTick()
    expect(wrapper.text()).toContain('受け付けるかは確かめられません')
    expect(wrapper.text()).toContain('必須のファイルの項目（invoice）')
    expect(wrapper.text()).not.toContain('この要求は受け付けられます')
  })

  it('入口・タスクの状態で断る・断りうる理由を出す', async () => {
    const base = {
      formatError: '',
      eventId: null,
      eventIdError: '',
      values: [],
      inputErrors: [],
      fileInputs: [],
      requiredFileInputs: [],
      canViewPrompt: false,
      prompt: null,
    }
    preview.mockResolvedValueOnce({
      ...base,
      outcome: TriggerPreviewOutcome.REFUSED,
      runProblems: ['この入口は停止中です'],
    })
    preview.mockResolvedValueOnce({
      ...base,
      outcome: TriggerPreviewOutcome.UNCONFIRMED,
      runProblems: ['タスクの定義に、実行を断られうる設定があります（タスクの管理者に確かめてください）'],
    })
    const wrapper = await mountSuspended(TriggerBindingPreview, { props: { bindingId: 'b1', settings } })
    const vm = wrapper.vm as unknown as { onSubmit: () => Promise<void> }
    await wrapper.find('textarea').setValue('{}')
    await vm.onSubmit()
    await nextTick()
    expect(wrapper.text()).toContain('受付で断ります')
    expect(wrapper.text()).toContain('この入口は停止中です')
    await vm.onSubmit()
    await nextTick()
    expect(wrapper.text()).toContain('受け付けるかは確かめられません')
    expect(wrapper.text()).toContain('実行を断られうる設定があります')
    expect(wrapper.text()).not.toContain('必須のファイルの項目')
  })

  it('読めないpayloadは、その理由だけを出す', async () => {
    preview.mockResolvedValue({
      outcome: TriggerPreviewOutcome.REFUSED,
      requiredFileInputs: [],
      runProblems: [],
      duplicate: false,
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
