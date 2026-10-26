import { describe, expect, it } from 'vitest'

const login = (allPermissions: string[]) => {
  useAccountStore().user = {
    id: 'u1',
    username: 'u1',
    email: 'u1@example.com',
    displayName: 'U1',
    isPlatformAdmin: false,
    allPermissions,
    departments: [],
  }
}

describe('useManageMenu', () => {
  it('入れ子の見出しの下に、権限のあるリンクだけを出す', () => {
    login([PermissionCode.CHANGE_CONNECTOR])
    const { items, links } = useManageMenu()
    expect(items.value).toHaveLength(1)
    const connector = items.value[0] as ManageMenuGroup
    expect(connector.label).toBe('Connector')
    const db = connector.children[0] as ManageMenuGroup
    expect(db.label).toBe('DB Connector')
    expect(db.children.map((item) => item.label)).toEqual(['Connector'])
    expect(links.value.map((link) => link.to)).toEqual(['/manage/connectors'])
  })

  it('表示できるリンクがない見出しは出さない', () => {
    login([PermissionCode.CHANGE_USER])
    const { items, links } = useManageMenu()
    expect(items.value.map((item) => item.label)).toEqual(['ユーザー'])
    expect(links.value.map((link) => link.to)).toEqual(['/manage/users'])
  })
})
