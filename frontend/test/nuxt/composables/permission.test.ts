import { describe, expect, it } from 'vitest'

// 画面で使っていないコードでも判定できることを確かめるため、型を外して任意のコードを渡す
const code = (value: string) => value as PermissionCode

describe('usePermission', () => {
  it('allPermissions に含まれるコードだけを持っているとみなす', () => {
    const account = useAccountStore()
    account.user = {
      id: 'u1',
      username: 'u1',
      email: 'u1@example.com',
      displayName: 'U1',
      allPermissions: ['skill.can_publish_company_skills'],
      departments: [],
      usableDepartments: [],
      manageableDepartments: [],
    }
    const { hasPermission, hasAny } = usePermission()
    expect(hasPermission(code('skill.can_publish_company_skills'))).toBe(true)
    expect(hasPermission(code('delivery.can_manage_destinations'))).toBe(false)
    expect(hasAny([code('delivery.can_manage_destinations'), code('skill.can_publish_company_skills')])).toBe(true)
    expect(hasAny([])).toBe(false)
  })

  it('未ログインなら何も持たない', () => {
    useAccountStore().user = null
    expect(usePermission().hasPermission(code('skill.can_publish_company_skills'))).toBe(false)
  })
})
