import { mockNuxtImport, mountSuspended } from '@nuxt/test-utils/runtime'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { ConnectorForm, ConnectorPermissionsForm, ConnectorTablesForm } from '#components'

const { create, patch, tables, groups } = vi.hoisted(() => ({
  create: vi.fn(),
  patch: vi.fn(),
  tables: vi.fn(),
  groups: vi.fn(),
}))

mockNuxtImport('useAPI', () => () => ({
  manage: {
    connectors: () => ({ create, patch, tables }),
    groups: { list: groups },
    departments: () => ({ list: vi.fn().mockResolvedValue({ results: [], next: null, previous: null }) }),
  },
}))

const connector: Connector = {
  id: 'c1',
  name: '販売管理DB',
  kind: ConnectorKind.SQLSERVER,
  kindDisplay: 'SQL Server',
  host: 'sales-db.internal',
  port: 1433,
  database: 'Sales',
  username: 'yoriki_reader',
  hasPassword: true,
  passwordHint: 'word',
  tls: TlsMode.VERIFY,
  connectTimeoutSeconds: 10,
  maxConcurrency: 4,
  isActive: true,
  version: 2,
  description: '販売の実績',
  defaultSchema: 'dbo',
  tables: [{ schema: 'dbo', name: 'Vendors', description: '取引先', columns: { VendorCode: '取引先コード' } }],
  maxRows: 1000,
  maxResultBytes: 1_500_000,
  timeoutSeconds: 30,
  isSensitive: false,
  permissions: [
    {
      id: 'p1',
      group: null,
      department: 'd1',
      departmentInfo: { id: 'd1', name: '経理部' },
      operations: ['datasource_describe', 'datasource_query'],
    },
  ],
  createdBy: 'u1',
  created: '2026-10-26T00:00:00Z',
  updated: '2026-10-26T00:00:00Z',
}

const flushPromises = () => new Promise((resolve) => setTimeout(resolve))

type Exposed = { onSubmit: () => Promise<void>; canPost: boolean }

describe('ConnectorForm', () => {
  beforeEach(() => {
    create.mockReset().mockResolvedValue(connector)
    patch.mockReset().mockResolvedValue(connector)
  })

  type Form = Exposed & {
    form: { name: string; kind: number; host: string; database: string; username: string; password: string }
  }

  it('作成では接続先とパスワードを送り、MySQLでは既定のスキーマを送らない', async () => {
    const wrapper = await mountSuspended(ConnectorForm, { props: { modelValue: null } })
    const vm = wrapper.vm as unknown as Form
    Object.assign(vm.form, {
      name: '基幹DB',
      kind: ConnectorKind.MYSQL,
      host: ' db.internal ',
      database: 'core',
      username: 'reader',
      password: 'secret',
    })
    await nextTick()
    expect(vm.canPost).toBe(true)
    await vm.onSubmit()
    const data = create.mock.calls[0]![0]
    expect(data).toMatchObject({ kind: ConnectorKind.MYSQL, host: 'db.internal', password: 'secret' })
    expect(data.maxResultBytes).toBe(10 * 1024 * 1024)
    expect(data.port).toBeUndefined()
    expect(data.defaultSchema).toBeUndefined()
  })

  it('更新では編集を始めた時点の版を送り、空のパスワードとMBで割り切れない上限は変えない', async () => {
    const wrapper = await mountSuspended(ConnectorForm, { props: { modelValue: connector } })
    await (wrapper.vm as unknown as Form).onSubmit()
    const data = patch.mock.calls[0]![0]
    expect(data).toMatchObject({ version: 2, maxResultBytes: 1_500_000, defaultSchema: 'dbo' })
    expect(data).not.toHaveProperty('password')
    expect(data).not.toHaveProperty('host')
    expect(wrapper.text()).toContain('末尾 word')
  })
})

describe('ConnectorTablesForm', () => {
  beforeEach(() => {
    patch.mockReset().mockResolvedValue(connector)
    tables.mockReset().mockResolvedValue({
      ok: true,
      detail: '',
      tables: [
        {
          schema: 'dbo',
          name: 'Vendors',
          kind: 'table',
          description: '',
          columns: [{ name: 'VendorCode', type: 'nvarchar', description: '' }],
        },
        { schema: 'dbo', name: 'Orders', kind: 'view', description: '受注', columns: [] },
      ],
    })
  })

  type Form = Exposed & {
    loadOnce: () => void
    selectedKeys: string[]
    form: { tables: { name: string; columns: Record<string, string> }[] }
    setColumnDescription: (entry: unknown, column: string, value: string | null) => void
  }

  it('接続先の一覧から表を選び、説明を付けた列だけを版として送る', async () => {
    const wrapper = await mountSuspended(ConnectorTablesForm, { props: { modelValue: connector } })
    const vm = wrapper.vm as unknown as Form
    expect(vm.canPost).toBe(false)
    vm.loadOnce()
    await flushPromises()
    expect(tables).toHaveBeenCalledWith(40) // 接続のタイムアウト10秒＋実行時間の上限30秒
    vm.selectedKeys = [...vm.selectedKeys, 'dbo\u0000Orders']
    vm.setColumnDescription(vm.form.tables[0], 'VendorCode', '')
    await nextTick()
    expect(vm.canPost).toBe(true)
    await vm.onSubmit()
    expect(patch.mock.calls[0]![0]).toEqual({
      version: 2,
      tables: [
        { schema: 'dbo', name: 'Vendors', description: '取引先', columns: {} },
        { schema: 'dbo', name: 'Orders', description: '受注', columns: {} },
      ],
      changeReason: undefined,
    })
  })
})

describe('ConnectorPermissionsForm', () => {
  beforeEach(() => {
    patch.mockReset().mockResolvedValue(connector)
    groups.mockReset().mockResolvedValue([{ name: '業務管理者' }])
  })

  type Form = Exposed & {
    addRow: () => void
    form: { permissions: { grantee: string; group: string | null; operations: string[] }[] }
  }

  it('役割を選ぶまでは保存できず、役割と部門のどちらか一方だけを送る', async () => {
    const wrapper = await mountSuspended(ConnectorPermissionsForm, { props: { modelValue: connector } })
    const vm = wrapper.vm as unknown as Form
    vm.addRow()
    await nextTick()
    expect(vm.canPost).toBe(false)
    vm.form.permissions[1]!.group = '業務管理者'
    vm.form.permissions[1]!.operations = ['datasource_describe']
    await nextTick()
    expect(vm.canPost).toBe(true)
    await vm.onSubmit()
    expect(patch.mock.calls[0]![0]).toEqual({
      version: 2,
      permissions: [
        { group: null, department: 'd1', operations: ['datasource_describe', 'datasource_query'] },
        { group: '業務管理者', department: null, operations: ['datasource_describe'] },
      ],
    })
  })
})
