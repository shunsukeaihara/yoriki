import { mockNuxtImport, mountSuspended } from '@nuxt/test-utils/runtime'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { AxiosError, AxiosHeaders } from 'axios'
import {
  ConnectorForm,
  ConnectorPermissionsForm,
  ConnectorQueryPanel,
  ConnectorTablesForm,
  DatabaseConnectionForm,
} from '#components'

const { create, patch, tables, groups, query, connectionCreate, connectionPatch } = vi.hoisted(() => ({
  create: vi.fn(),
  patch: vi.fn(),
  tables: vi.fn(),
  groups: vi.fn(),
  query: vi.fn(),
  connectionCreate: vi.fn(),
  connectionPatch: vi.fn(),
}))

mockNuxtImport('useAPI', () => () => ({
  manage: {
    connectors: () => ({ create, patch, query }),
    databaseConnections: () => ({
      create: connectionCreate,
      patch: connectionPatch,
      tables,
      list: vi.fn().mockResolvedValue({ results: [], next: null, previous: null }),
    }),
    groups: { list: groups },
    departments: () => ({ list: vi.fn().mockResolvedValue({ results: [], next: null, previous: null }) }),
  },
}))

const connection: DatabaseConnection = {
  id: 'd1',
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
  connectorCount: 1,
  createdBy: 'u1',
  created: '2026-10-26T00:00:00Z',
  updated: '2026-10-26T00:00:00Z',
}

const connector: Connector = {
  id: 'c1',
  name: '取引先マスタ',
  connection: 'd1',
  connectionInfo: {
    id: 'd1',
    name: '販売管理DB',
    kind: ConnectorKind.SQLSERVER,
    kindDisplay: 'SQL Server',
    host: 'sales-db.internal',
    port: 1433,
    database: 'Sales',
    connectTimeoutSeconds: 10,
    isActive: true,
  },
  kind: ConnectorKind.SQLSERVER,
  kindDisplay: 'SQL Server',
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

describe('DatabaseConnectionForm', () => {
  beforeEach(() => {
    connectionCreate.mockReset().mockResolvedValue(connection)
    connectionPatch.mockReset().mockResolvedValue(connection)
  })

  type Form = Exposed & {
    form: { name: string; kind: number; host: string; database: string; username: string; password: string }
  }

  it('作成では接続先とパスワードを送る。ポートは空なら送らない', async () => {
    const wrapper = await mountSuspended(DatabaseConnectionForm, { props: { modelValue: null } })
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
    const data = connectionCreate.mock.calls[0]![0]
    expect(data).toMatchObject({ kind: ConnectorKind.MYSQL, host: 'db.internal', password: 'secret' })
    expect(data.port).toBeUndefined()
  })

  it('更新では空のパスワードを送らず、ホスト等を変えたらパスワードを入れるまで保存できない', async () => {
    const wrapper = await mountSuspended(DatabaseConnectionForm, { props: { modelValue: connection } })
    const vm = wrapper.vm as unknown as Form
    expect(wrapper.text()).toContain('末尾 word')
    await vm.onSubmit()
    expect(connectionPatch.mock.calls[0]![0]).not.toHaveProperty('password')
    expect(connectionPatch.mock.calls[0]![0]).not.toHaveProperty('kind')

    vm.form.host = 'sales-db2.internal'
    await nextTick()
    expect(vm.canPost).toBe(false)
    expect(wrapper.text()).toContain('ホスト・ポート・DB名を変えるときは、パスワードを入力し直します')
    vm.form.password = 'new-pass'
    await nextTick()
    expect(vm.canPost).toBe(true)
  })
})

describe('ConnectorForm', () => {
  beforeEach(() => {
    create.mockReset().mockResolvedValue(connector)
    patch.mockReset().mockResolvedValue(connector)
  })

  type Form = Exposed & { form: { name: string; connection: string | null } }

  it('作成では使うDB接続を送り、DB接続を選ぶまで保存できない', async () => {
    const wrapper = await mountSuspended(ConnectorForm, { props: { modelValue: null } })
    const vm = wrapper.vm as unknown as Form
    vm.form.name = '受注実績'
    await nextTick()
    expect(vm.canPost).toBe(false)
    vm.form.connection = 'd1'
    await nextTick()
    await vm.onSubmit()
    const data = create.mock.calls[0]![0]
    expect(data).toMatchObject({ name: '受注実績', connection: 'd1', maxResultBytes: 10 * 1024 * 1024 })
    expect(data.defaultSchema).toBe('')
  })

  it('更新では編集を始めた時点の版を送り、DB接続とMBで割り切れない上限は変えない', async () => {
    const wrapper = await mountSuspended(ConnectorForm, { props: { modelValue: connector } })
    await (wrapper.vm as unknown as Form).onSubmit()
    const data = patch.mock.calls[0]![0]
    expect(data).toMatchObject({ version: 2, maxResultBytes: 1_500_000, defaultSchema: 'dbo' })
    expect(data).not.toHaveProperty('connection')
    expect(wrapper.text()).toContain('販売管理DB')
  })

  it('別のタブの保存でConnectorが変わっても、未保存の入力を残して版だけを最新にする。reset() で捨てる', async () => {
    const wrapper = await mountSuspended(ConnectorForm, { props: { modelValue: connector } })
    const vm = wrapper.vm as unknown as Form & { form: { version: number }; reset: () => void }
    vm.form.name = '取引先マスタ（本番）'
    await wrapper.setProps({ modelValue: { ...connector, version: 3, tables: [] } })
    expect(vm.form).toMatchObject({ name: '取引先マスタ（本番）', version: 3 })
    vm.reset()
    expect(vm.form).toMatchObject({ name: '取引先マスタ', version: 3 })
  })

  it('未保存の入力がなければ、変わったConnectorに合わせ直す', async () => {
    const wrapper = await mountSuspended(ConnectorForm, { props: { modelValue: connector } })
    await wrapper.setProps({ modelValue: { ...connector, version: 3, description: '新しい説明' } })
    expect((wrapper.vm as unknown as { form: object }).form).toMatchObject({ version: 3, description: '新しい説明' })
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
    expect(tables).toHaveBeenCalledWith(40) // DB接続の接続のタイムアウト10秒＋DBでの実行時間30秒
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

describe('ConnectorQueryPanel', () => {
  beforeEach(() => {
    query.mockReset()
  })

  it('サーバーが断った引数のエラーを、何番目の引数かを付けて欄に出す', async () => {
    const response = {
      status: 400,
      statusText: 'Bad Request',
      headers: { 'content-type': 'application/json' },
      config: { headers: new AxiosHeaders() },
      data: { params: { '1': ['引数は、文字・数値・真偽値・nullにしてください'] } },
    }
    query.mockRejectedValue(new AxiosError('400', 'ERR_BAD_REQUEST', undefined, undefined, response))
    const wrapper = await mountSuspended(ConnectorQueryPanel, { props: { connector } })
    const vm = wrapper.vm as unknown as { form: { sql: string; paramsText: string }; onRun: () => Promise<void> }
    vm.form.sql = 'SELECT * FROM Vendors WHERE VendorCode = ? AND Region = ?'
    vm.form.paramsText = '["A01", {"a": 1}]'
    await vm.onRun()
    expect(query.mock.calls[0]).toEqual([
      { sql: 'SELECT * FROM Vendors WHERE VendorCode = ? AND Region = ?', params: ['A01', { a: 1 }] },
      40,
    ])
    await nextTick()
    expect(wrapper.text()).toContain('2番目：引数は、文字・数値・真偽値・nullにしてください')
  })
})
