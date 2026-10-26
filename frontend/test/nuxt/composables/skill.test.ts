import { describe, expect, it } from 'vitest'

const t = (key: string) => key

describe('parseSchemaText', () => {
  it('空ならnull、JSONなら値、読めなければエラーの文', () => {
    expect(parseSchemaText('  ', t)).toEqual({ value: null, error: null })
    expect(parseSchemaText('{"type": "object"}', t)).toEqual({ value: { type: 'object' }, error: null })
    expect(parseSchemaText('{"type": ', t).error).toBe('JSONとして読めません')
  })

  it('schemaToText と往復できる', () => {
    const schema = { type: 'object', properties: { ok: { type: 'boolean' } } }
    expect(parseSchemaText(schemaToText(schema), t).value).toEqual(schema)
    expect(schemaToText(null)).toBe('')
  })
})

describe('skillFieldLabel', () => {
  it('変更点の項目名（snake_case）を表示名にする', () => {
    expect(skillFieldLabel('output_schema', t)).toBe('業務結果のSchema')
    expect(skillFieldLabel('unknown', t)).toBe('unknown')
  })
})

describe('skillChangeLines', () => {
  const tp = (key: string, params?: Record<string, unknown>) =>
    key.replace(/\{(\w+)\}/g, (_m, name: string) => String(params?.[name] ?? ''))

  it('変わった項目と資料の key を行にする', () => {
    expect(
      skillChangeLines({ fields: ['instructions', 'tools'], assetsReplaced: ['vendor-master'], assetsAdded: [] }, tp),
    ).toEqual(['手順・使ってよいTool', '資料の置換：vendor-master'])
    expect(skillChangeLines(null, tp)).toEqual([])
  })
})

describe('skillOwnerLabel', () => {
  const base = {
    id: 's1',
    name: '請求書の照合',
    version: 1,
    description: '',
    suspended: false,
    draft: null,
    canEdit: false,
    canPublish: false,
    created: '2026-10-24T00:00:00Z',
    updated: '2026-10-24T00:00:00Z',
  }
  it('個人は持ち主、部門は部門名、全社は「全社」、システムは「システム」', () => {
    const owner = { id: 'u1', email: 'taro@example.co.jp', displayName: '' }
    expect(
      skillOwnerLabel({ ...base, scope: 1, scopeDisplay: '個人', ownerInfo: owner, departmentInfo: null }, t),
    ).toBe('taro@example.co.jp')
    expect(
      skillOwnerLabel(
        { ...base, scope: 2, scopeDisplay: '部門', ownerInfo: null, departmentInfo: { id: 'd1', name: '経理部' } },
        t,
      ),
    ).toBe('経理部')
    expect(skillOwnerLabel({ ...base, scope: 3, scopeDisplay: '全社', ownerInfo: null, departmentInfo: null }, t)).toBe(
      '全社',
    )
    expect(
      skillOwnerLabel({ ...base, scope: 4, scopeDisplay: 'システム', ownerInfo: null, departmentInfo: null }, t),
    ).toBe('システム')
  })
})

describe('useSkillCreatableScopes', () => {
  const login = (allPermissions: string[], manageable: { id: string; name: string }[] = []) => {
    useAccountStore().user = {
      id: 'u1',
      username: 'u1',
      email: 'u1@example.com',
      displayName: 'U1',
      allPermissions,
      departments: manageable,
      usableDepartments: manageable,
      manageableDepartments: manageable,
    }
  }
  const accounting = { id: 'd1', name: '経理部' }

  it('個人はいつでも作れる。部門は管理できる部門と部門のpermissionの両方が要る', () => {
    login([], [accounting])
    expect(useSkillCreatableScopes().scopes.value).toEqual([SkillScope.PERSONAL])
    login([PermissionCode.EDIT_DEPARTMENT_SKILLS])
    expect(useSkillCreatableScopes().scopes.value).toEqual([SkillScope.PERSONAL])
    login([PermissionCode.EDIT_DEPARTMENT_SKILLS], [accounting])
    const { scopes, departments } = useSkillCreatableScopes()
    expect(scopes.value).toEqual([SkillScope.PERSONAL, SkillScope.DEPARTMENT])
    expect(departments.value).toEqual([accounting])
  })

  it('全社・システムはそれぞれのpermissionで作れる', () => {
    login([PermissionCode.PUBLISH_COMPANY_SKILLS, PermissionCode.EDIT_SYSTEM_SKILLS])
    expect(useSkillCreatableScopes().scopes.value).toEqual([SkillScope.PERSONAL, SkillScope.COMPANY, SkillScope.SYSTEM])
  })
})
