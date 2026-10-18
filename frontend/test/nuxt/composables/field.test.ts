import { describe, expect, it } from 'vitest'

describe('getProp', () => {
  const item = { id: 'a', agent: { name: 'Bot' } }

  it('プロパティ名・パス・関数で値を取り出す', () => {
    expect(getProp(item, 'id')).toBe('a')
    expect(getProp(item, 'agent.name')).toBe('Bot')
    expect(getProp(item, (i: typeof item) => i.agent.name.toUpperCase())).toBe('BOT')
  })

  it('accessor がなければ item をそのまま返し、途中で途切れたパスは undefined にする', () => {
    expect(getProp(item, undefined)).toBe(item)
    expect(getProp(item, 'missing.name')).toBeUndefined()
    expect(getProp('text', 'length')).toBe('text')
  })
})
