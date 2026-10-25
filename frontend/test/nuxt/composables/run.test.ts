import { describe, expect, it } from 'vitest'

const run = (overrides: Partial<Run>): Run => ({
  id: 'r1',
  conversation: 'c1',
  kind: RunKind.WORK,
  status: RunStatus.COMPLETED,
  statusDisplay: '完了',
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
    // Skill を使った Run は、使った版と業務結果を出す
    expect(hasWorkToShow(run({ skills: [{ skill: 's1', name: '請求書の照合', number: 3, chosen: true }] }))).toBe(true)
    expect(hasWorkToShow(run({ result: { verdict: 'ok' } }))).toBe(true)
    expect(hasWorkToShow(run({ status: RunStatus.FAILED }))).toBe(true)
    expect(hasWorkToShow(run({ todos: [{ content: '読む', status: TodoStatus.COMPLETED }] }))).toBe(true)
    // Skill の編集の Run は、1回の応答で終わっても編集の Run であることを出す
    expect(hasWorkToShow(run({ kind: RunKind.SKILL_EDIT }))).toBe(true)
  })
})

describe('toolLabel', () => {
  const t = (key: string, params?: Record<string, unknown>) =>
    key.replace(/\{(\w+)\}/g, (_m, name: string) => String(params?.[name] ?? ''))

  it('shell はエージェントが書いた説明を出す', () => {
    expect(toolLabel({ name: 'shell', arguments: { description: '表を抽出', command: 'python a.py' } }, t)).toBe(
      '表を抽出',
    )
    expect(toolLabel({ name: 'shell', arguments: { command: 'ls' } }, t)).toBe('コマンドを実行')
    expect(toolLabel({ name: 'skill', arguments: { name: '請求書の照合' } }, t)).toBe('Skillを読み込む：請求書の照合')
  })

  it('Skill 管理の Tool は対象の資料を出す', () => {
    expect(toolLabel({ name: 'stage_skill_asset', arguments: { key: 'vendor-master', path: '/x' } }, t)).toBe(
      'Skillの資料を登録：vendor-master',
    )
    expect(toolLabel({ name: 'publish_skill_draft', arguments: {} }, t)).toBe('Skillを公開')
  })

  it('ファイルの Tool は対象のパスを出す', () => {
    expect(toolLabel({ name: 'read', arguments: { path: '/workspace/input/a.pdf' } }, t)).toBe(
      'ファイルを読む：/workspace/input/a.pdf',
    )
  })

  it('知らない Tool は名前をそのまま出す', () => {
    expect(toolLabel({ name: 'other', arguments: {} }, t)).toBe('other')
  })
})
