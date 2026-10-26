import { describe, expect, it } from 'vitest'

describe('結果のサイズの上限', () => {
  it('MBとバイトを変換し、MBを変えていなければ元のバイト数を返す', () => {
    expect(bytesToMb(10 * 1024 * 1024)).toBe(10)
    expect(mbToBytes(20)).toBe(20 * 1024 * 1024)
    // MBで割り切れない値は、入力を変えていなければそのまま（保存のたびに新しい版を作らない）
    expect(mbToBytes(bytesToMb(1_500_000), 1_500_000)).toBe(1_500_000)
    expect(mbToBytes(2, 1_500_000)).toBe(2 * 1024 * 1024)
  })
})

describe('parseQueryParams', () => {
  const t = (key: string) => key

  it('空なら引数なし、JSONの配列なら値にする', () => {
    expect(parseQueryParams('  ', t)).toEqual({ value: [], error: null })
    expect(parseQueryParams('["A01", 10, null]', t)).toEqual({ value: ['A01', 10, null], error: null })
  })

  it('配列でないJSONや読めない文字は、エラーの文を返す', () => {
    expect(parseQueryParams('{"a": 1}', t).error).toBeTruthy()
    expect(parseQueryParams('[A01', t).error).toBeTruthy()
  })
})

describe('tableLabel', () => {
  it('スキーマがあれば付け、MySQL（スキーマなし）は名前だけにする', () => {
    expect(tableLabel({ schema: 'dbo', name: 'Vendors' })).toBe('dbo.Vendors')
    expect(tableLabel({ schema: '', name: 'vendors' })).toBe('vendors')
  })
})
