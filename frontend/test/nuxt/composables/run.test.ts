import { describe, expect, it } from 'vitest'

const run = (overrides: Partial<Run>): Run => ({
  id: 'r1',
  conversation: 'c1',
  status: RunStatus.COMPLETED,
  statusDisplay: '完了',
  endReason: null,
  endReasonDisplay: null,
  error: '',
  modelProfileName: 'モデル',
  effort: '',
  stepCount: 1,
  todos: [],
  created: '2026-10-21T00:00:00Z',
  updated: '2026-10-21T00:00:00Z',
  finished: null,
  ...overrides,
})

describe('run', () => {
  it('停止中の Run は、もう停止を依頼できないが実行中として扱う', () => {
    expect(isCancellable({ status: RunStatus.CANCELLING })).toBe(false)
    expect(isActiveRun({ status: RunStatus.CANCELLING })).toBe(true)
    expect(isCancellable({ status: RunStatus.WAITING_USER })).toBe(true)
  })

  it('1回の応答で終わった Run には、作業の表示を出さない', () => {
    expect(hasWorkToShow(run({}))).toBe(false)
    expect(hasWorkToShow(run({ stepCount: 2 }))).toBe(true)
    expect(hasWorkToShow(run({ status: RunStatus.FAILED }))).toBe(true)
    expect(hasWorkToShow(run({ todos: [{ content: '読む', status: TodoStatus.COMPLETED }] }))).toBe(true)
  })
})
