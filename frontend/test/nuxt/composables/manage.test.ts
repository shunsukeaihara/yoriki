import { describe, expect, it } from 'vitest'

const login = (allPermissions: string[]) => {
  useAccountStore().user = {
    id: 'u1',
    username: 'u1',
    email: 'u1@example.com',
    displayName: 'U1',
    allPermissions,
    departments: [],
    usableDepartments: [],
    manageableDepartments: [],
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

  it('見るだけのpermissionでも、そのページに入れる（追加・削除だけでは入れない）', () => {
    login([PermissionCode.VIEW_CONNECTOR, PermissionCode.VIEW_DEPARTMENT, PermissionCode.ADD_LLM_CONNECTION])
    const { links } = useManageMenu()
    expect(links.value.map((link) => link.to)).toEqual(['/manage/connectors', '/manage/departments'])
  })

  it('システムのSkillは、システムのSkillのpermissionを持つ人にだけ出す', () => {
    login([PermissionCode.PUBLISH_COMPANY_SKILLS, PermissionCode.EDIT_DEPARTMENT_SKILLS])
    expect(useManageMenu().links.value).toEqual([])
    login([PermissionCode.EDIT_SYSTEM_SKILLS])
    expect(useManageMenu().links.value.map((link) => link.to)).toEqual(['/manage/system_skills'])
  })
})

describe('useManageMenu（連携）', () => {
  it('入口と出口は「連携」の見出しの下に、それぞれのpermissionで出す', () => {
    login([PermissionCode.VIEW_TRIGGER_BINDING])
    const { items, links } = useManageMenu()
    expect(items.value.map((item) => item.label)).toEqual(['連携'])
    expect(links.value.map((link) => link.to)).toEqual(['/manage/trigger_bindings'])
    login([PermissionCode.CHANGE_DESTINATION, PermissionCode.CHANGE_TRIGGER_BINDING])
    expect(useManageMenu().links.value.map((link) => link.to)).toEqual([
      '/manage/trigger_bindings',
      '/manage/destinations',
    ])
    // 作るだけのpermissionでは入れない
    login([PermissionCode.ADD_DESTINATION])
    expect(useManageMenu().links.value).toEqual([])
  })
})
