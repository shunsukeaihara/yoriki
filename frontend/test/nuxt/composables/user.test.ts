import { describe, expect, it } from 'vitest'

const t = (key: string) => key
const base = { hasPassword: false, ssoProviders: [] as string[] } as unknown as ManageUser

describe('loginMethodLabel', () => {
  it('SSO の認証基盤があればその名前、なければパスワードを設定済みか', () => {
    expect(loginMethodLabel({ ...base, ssoProviders: ['microsoft', 'google'] }, t)).toBe(
      'Microsoft 365、Google Workspace',
    )
    expect(loginMethodLabel({ ...base, hasPassword: true }, t)).toBe('パスワード')
    expect(loginMethodLabel(base, t)).toBe('未設定')
  })
})
