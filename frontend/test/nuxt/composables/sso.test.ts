import { describe, expect, it } from 'vitest'

describe('parseDomains', () => {
  it('改行・カンマ・空白で区切り、空の行を除く', () => {
    expect(parseDomains('example.co.jp\n\n sub.example.co.jp , other.co.jp、x.jp')).toEqual([
      'example.co.jp',
      'sub.example.co.jp',
      'other.co.jp',
      'x.jp',
    ])
    expect(parseDomains('')).toEqual([])
    expect(parseDomains(null)).toEqual([])
  })
})

describe('ssoCallbackUrl', () => {
  it('画面と同じオリジンの allauth のコールバック', () => {
    expect(ssoCallbackUrl(SSOKind.GOOGLE)).toBe(`${window.location.origin}/api/accounts/google/login/callback/`)
  })
})
