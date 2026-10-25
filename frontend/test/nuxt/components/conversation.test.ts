import { mockNuxtImport, mountSuspended } from '@nuxt/test-utils/runtime'
import { flushPromises } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { ConversationAttachmentChips, ConversationStartForm, RunClarificationForm } from '#components'

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
