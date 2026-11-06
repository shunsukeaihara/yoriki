import { mockNuxtImport, mountSuspended } from '@nuxt/test-utils/runtime'
import { flushPromises } from '@vue/test-utils'
import { describe, expect, it, vi } from 'vitest'
import { RunWork } from '#components'

const { list } = vi.hoisted(() => ({ list: vi.fn(() => Promise.resolve({ results: [], next: null, previous: null })) }))

mockNuxtImport('useAPI', () => () => ({ runs: () => ({ scriptCalls: () => ({ list }), events: () => ({ list }) }) }))

const run = (status: RunStatusEnum, statusDisplay: string) =>
  ({
    id: 'r1',
    conversation: 'c1',
    kind: RunKind.WORK,
    trigger: RunTrigger.CONVERSATION,
    taskInfo: null,
    status,
    statusDisplay,
    endReason: null,
    endReasonDisplay: null,
    error: '',
    modelProfileName: 'モデル',
    effort: '',
    stepCount: 1,
    todos: [],
    skills: [],
    result: null,
    skillChanges: [],
    taskChanges: [],
    created: '2026-11-06T00:00:00Z',
    updated: '2026-11-06T00:00:00Z',
    finished: null,
  }) as Run

const reply = {
  type: 'run.message',
  kind: RunEventKind.ASSISTANT_MESSAGE,
  run: 'r1',
  sequence: 1,
  text: '始めました',
  toolCalls: [],
} as unknown as RunStreamEvent

const waiting = {
  type: 'run.status',
  run: 'r1',
  sequence: 2,
  status: RunStatus.WAITING_BACKGROUND,
} as unknown as RunStreamEvent

const mount = async (status: RunStatusEnum, display: string, events: RunStreamEvent[]) => {
  const wrapper = await mountSuspended(RunWork, { props: { run: run(status, display), events } })
  await flushPromises()
  return wrapper
}

describe('RunWork', () => {
  it('Run が終わるまでは、最後の応答を書きかけとして出す', async () => {
    const wrapper = await mount(RunStatus.RUNNING, '作業中', [reply])
    expect(wrapper.text()).toContain('始めました')
  })

  it('裏の作業を待つ間は、応答が会話のメッセージになるので重ねて出さない', async () => {
    const wrapper = await mount(RunStatus.WAITING_BACKGROUND, '裏で作業を待っています', [reply, waiting])
    expect(wrapper.text()).toContain('裏で作業を待っています')
    expect(wrapper.text()).not.toContain('始めました')
  })
})
