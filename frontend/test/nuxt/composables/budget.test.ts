import { describe, expect, it } from 'vitest'

describe('formatUsd', () => {
  it('formats dollars and treats an empty value as unlimited', () => {
    expect(formatUsd('1234.5', '無制限')).toBe('$1,234.50')
    expect(formatUsd('-30.0000', '無制限')).toBe('-$30.00')
    expect(formatUsd(null, '無制限')).toBe('無制限')
  })
})

describe('formatRunCount', () => {
  it('shows the count against the limit', () => {
    expect(formatRunCount(1200, 5000, '無制限')).toBe('1,200 / 5,000')
    expect(formatRunCount(3, null, '無制限')).toBe('3 / 無制限')
    expect(formatRunCount(0, 0, '無制限')).toBe('0 / 0')
  })
})

describe('budgetUsedPercent', () => {
  it('counts what is spent or reserved against the limit', () => {
    expect(budgetUsedPercent({ limit: '100.00', remaining: '70.0000' })).toBe(30)
    expect(budgetUsedPercent({ limit: '100.00', remaining: '-30.0000' })).toBe(100)
    expect(budgetUsedPercent({ limit: '0.00', remaining: '0.0000' })).toBe(100)
    expect(budgetUsedPercent({ limit: null, remaining: null })).toBeNull()
  })
})
