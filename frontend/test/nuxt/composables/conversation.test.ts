import { describe, expect, it } from 'vitest'

const message = (id: string, role: MessageRoleEnum, run: string | null, instruction: Message['instruction'] = null) =>
  ({
    id,
    run,
    sender: null,
    role,
    body: id,
    instruction,
    created: `2026-10-21T00:00:0${id.slice(1)}Z`,
  }) satisfies Message

const ids = (items: TimelineItem[]) => items.map((i) => i.id)

describe('buildTimeline', () => {
  it('作業の表示は、エージェントの応答の直前に置く', () => {
    const items = buildTimeline([
      message('m1', MessageRole.USER, 'r1'),
      message('m2', MessageRole.USER, 'r1', {
        kind: InstructionKind.CURRENT,
        state: InstructionState.APPLIED,
        stateDisplay: '反映済み',
      }),
      message('m3', MessageRole.AGENT, 'r1'),
    ])
    expect(ids(items)).toEqual(['m1', 'm2', 'run-r1', 'm3'])
  })

  it('応答のない Run は、利用者のメッセージの最後の後に置き、待っている次の依頼はその後に残す', () => {
    const items = buildTimeline([
      message('m1', MessageRole.USER, 'r1'),
      message('m2', MessageRole.USER, 'r1', {
        kind: InstructionKind.NEXT,
        state: InstructionState.RECEIVED,
        stateDisplay: '受付済み',
      }),
    ])
    expect(ids(items)).toEqual(['m1', 'run-r1', 'm2'])
  })

  it('Run ごとに1つずつ置く', () => {
    const items = buildTimeline([
      message('m1', MessageRole.USER, 'r1'),
      message('m2', MessageRole.AGENT, 'r1'),
      message('m3', MessageRole.USER, 'r2'),
      message('m4', MessageRole.AGENT, 'r2'),
    ])
    expect(ids(items)).toEqual(['m1', 'run-r1', 'm2', 'm3', 'run-r2', 'm4'])
  })
})
