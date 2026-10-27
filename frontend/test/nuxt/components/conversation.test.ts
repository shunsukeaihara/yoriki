import { mockNuxtImport, mountSuspended } from '@nuxt/test-utils/runtime'
import { flushPromises } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import {
  ConversationAttachmentChips,
  ConversationConnectorPicker,
  ConversationPrompt,
  ConversationStartForm,
  RunClarificationForm,
} from '#components'

const { createConversation, createMessage, answer } = vi.hoisted(() => ({
  createConversation: vi.fn(),
  createMessage: vi.fn(),
  answer: vi.fn(),
}))

mockNuxtImport('useAPI', () => () => ({
  conversations: () => ({ create: createConversation, messages: () => ({ create: createMessage }) }),
  runs: () => ({ clarifications: () => ({ answer }) }),
}))

const model = (id: string, isLocal: boolean, isDefault = false): AppModelProfile => ({
  id,
  name: id,
  isDefault,
  isLocal,
  supportsTools: true,
  supportsImages: false,
  supportsDocuments: false,
  supportsReasoning: false,
  effortValues: [],
  defaultEffort: '',
})
const models = [model('cloud', false, true), model('local', true)]

type StartForm = { form: { handlesSensitiveData: boolean | null; modelProfile: string | null; body: string } }

describe('ConversationStartForm', () => {
  beforeEach(() => {
    createConversation.mockReset().mockResolvedValue({ id: 'c1' })
    createMessage.mockReset().mockResolvedValue({ id: 'm1' })
  })

  it('個人情報・機密情報を扱うと答えたら、ローカルLLMのモデルを選び直す', async () => {
    const wrapper = await mountSuspended(ConversationStartForm, { props: { models } })
    const vm = wrapper.vm as unknown as StartForm
    vm.form.handlesSensitiveData = false
    await nextTick()
    expect(vm.form.modelProfile).toBe('cloud')
    vm.form.handlesSensitiveData = true
    await nextTick()
    expect(vm.form.modelProfile).toBe('local')
  })

  it('会話を作ってから、最初の依頼を送る', async () => {
    const wrapper = await mountSuspended(ConversationStartForm, { props: { models } })
    const vm = wrapper.vm as unknown as StartForm
    vm.form.handlesSensitiveData = true
    await nextTick()
    await wrapper.find('textarea').setValue('名寄せして')
    await wrapper.find('form').trigger('submit')
    await flushPromises()
    expect(createConversation).toHaveBeenCalledWith({ handlesSensitiveData: true, modelProfile: 'local' })
    expect(createMessage).toHaveBeenCalledWith(expect.objectContaining({ body: '名寄せして', mode: MessageMode.AUTO }))
    expect(wrapper.emitted('started')?.[0]?.[0]).toEqual({ id: 'c1' })
  })
})

const clarification: Clarification = {
  id: 'q1',
  run: 'r1',
  kind: ClarificationKind.QUESTION,
  status: ClarificationStatus.OPEN,
  target: null,
  questions: [
    {
      header: '取引先',
      question: '支店は別にしますか',
      options: [
        { label: '別にする', description: '' },
        { label: 'まとめる', description: '' },
      ],
      multiSelect: false,
    },
    {
      header: '出力',
      question: '形式は',
      options: [
        { label: 'CSV', description: '' },
        { label: 'Excel', description: '' },
      ],
      multiSelect: true,
    },
  ],
  answers: [],
  comment: '',
  answeredBy: null,
  answered: null,
  expires: null,
  created: '2026-10-21T00:00:00Z',
}

describe('RunClarificationForm', () => {
  beforeEach(() => answer.mockReset().mockResolvedValue({ ...clarification, status: ClarificationStatus.ANSWERED }))

  it('質問と同じ順に、選んだ選択肢と自由記述を送る', async () => {
    const wrapper = await mountSuspended(RunClarificationForm, { props: { runId: 'r1', clarification } })
    await wrapper.findAll('button[role="radio"]')[0]!.trigger('click')
    const checkboxes = wrapper.findAll('button[role="checkbox"]')
    await checkboxes[0]!.trigger('click')
    await checkboxes[1]!.trigger('click')
    await wrapper.findAll('input[type="text"]')[1]!.setValue('PDFも')
    await wrapper
      .findAll('button')
      .find((b) => b.text() === '回答する')!
      .trigger('click')
    await flushPromises()
    expect(answer).toHaveBeenCalledWith({
      answers: [
        { selected: ['別にする'], text: '' },
        { selected: ['CSV', 'Excel'], text: 'PDFも' },
      ],
      comment: '',
    })
    expect(wrapper.emitted('answered')).toHaveLength(1)
  })
})

describe('ConversationAttachmentChips', () => {
  const item = (key: string, error = ''): PendingAttachment => ({
    key,
    file: new File(['x'], `${key}.csv`),
    artifact: null,
    uploading: false,
    error,
  })

  it('アップロードに失敗した添付だけ、送り直せる', async () => {
    const wrapper = await mountSuspended(ConversationAttachmentChips, {
      props: { items: [item('ok'), item('ng', 'ファイルが大きすぎます')] },
    })
    const retry = wrapper.findAll('button[aria-label="アップロードし直す"]')
    expect(retry).toHaveLength(1)
    await retry[0]!.trigger('click')
    expect(wrapper.emitted('retry')).toHaveLength(1)
    await wrapper.findAll('button[aria-label="添付を外す"]')[1]!.trigger('click')
    expect(wrapper.emitted('remove')).toEqual([['ng']])
  })
})

const connector = (id: string, isSensitive = false): AppConnector => ({
  id,
  name: id,
  kindDisplay: 'PostgreSQL',
  description: `${id}の説明`,
  isSensitive,
  scope: 3,
  scopeDisplay: '全社',
  departmentInfo: null,
})

type PickerItem = { id: string; disabled: boolean; description?: string; onSelect: () => void }
type Picker = { groups: { items: PickerItem[] }[] }

describe('ConversationConnectorPicker', () => {
  it('機密のConnectorは、ローカルLLMのモデルの会話でだけ選べる', async () => {
    const connectors = [connector('sales'), connector('hr', true)]
    const cloud = await mountSuspended(ConversationConnectorPicker, {
      props: { connectors, selected: [], localModel: false },
    })
    const items = (cloud.vm as unknown as Picker).groups[0]!.items
    expect(items.map((i) => [i.id, i.disabled])).toEqual([
      ['sales', false],
      ['hr', true],
    ])
    expect(items[1]!.description).toContain('ローカルLLM')

    const local = await mountSuspended(ConversationConnectorPicker, {
      props: { connectors, selected: ['sales'], localModel: true },
    })
    const [hr] = (local.vm as unknown as Picker).groups[0]!.items
    expect(hr!.id).toBe('hr')
    expect(hr!.disabled).toBe(false)
    hr!.onSelect()
    expect(local.emitted('select')?.[0]).toEqual([connectors[1]])
  })
})

describe('ConversationPrompt', () => {
  it('行の始めで @ を打つと、その文字を消してConnectorの一覧を開く', async () => {
    const wrapper = await mountSuspended(ConversationPrompt, {
      props: { modelValue: '', connectors: [connector('sales')] },
    })
    // 案内の文の `@` はそのまま出る（vue-i18n のリンクの記法として読まない）
    expect(wrapper.html()).toContain('@ でConnectorを指定')
    await wrapper.setProps({ modelValue: '@' })
    await flushPromises()
    expect(wrapper.emitted('update:modelValue')?.at(-1)).toEqual([''])
    expect((wrapper.vm as unknown as { connectorPickerOpen: boolean }).connectorPickerOpen).toBe(true)
  })

  it('Connectorを指定した依頼は、実行中なら送り方を選ばずに次の依頼にする', async () => {
    const run = { id: 'r1', status: 2 } as unknown as Run
    const wrapper = await mountSuspended(ConversationPrompt, {
      props: { modelValue: '取引先', activeRun: run, selectedConnectors: [connector('sales')] },
    })
    expect((wrapper.vm as unknown as { separateRequest: boolean }).separateRequest).toBe(true)
    expect(wrapper.text()).toContain('sales')
  })
})
