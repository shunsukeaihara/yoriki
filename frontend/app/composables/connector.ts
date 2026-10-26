type T = (key: string) => string

// バックエンドの connector.models.ConnectorKind と同じ値
export const ConnectorKind = {
  POSTGRESQL: 1,
  MYSQL: 2,
  SQLSERVER: 3,
} as const satisfies Record<string, ConnectorKindEnum>
export type ConnectorKind = (typeof ConnectorKind)[keyof typeof ConnectorKind]

// バックエンドの connector.models.TLSMode と同じ値
export const TlsMode = {
  DISABLE: 1,
  REQUIRE: 2,
  VERIFY: 3,
} as const satisfies Record<string, TlsEnum>
export type TlsMode = (typeof TlsMode)[keyof typeof TlsMode]

// バックエンドの connector.models.Operation と同じ値（Toolの名前）
export const ConnectorOperation = {
  DATASOURCE_DESCRIBE: 'datasource_describe',
  DATASOURCE_QUERY: 'datasource_query',
} as const satisfies Record<string, ConnectorOperationEnum>
export type ConnectorOperation = (typeof ConnectorOperation)[keyof typeof ConnectorOperation]

/** 種別の既定のポート（ConnectorKind.default_port）。ポートを空欄で作成すると、サーバーがこの値にする */
export const defaultPort = (kind: ConnectorKind): number =>
  ({ [ConnectorKind.POSTGRESQL]: 5432, [ConnectorKind.MYSQL]: 3306, [ConnectorKind.SQLSERVER]: 1433 })[kind]

/** 種別の既定のスキーマ（ConnectorKind.default_schema）。MySQLはDB名がスキーマなので空 */
export const defaultSchema = (kind: ConnectorKind): string =>
  ({ [ConnectorKind.POSTGRESQL]: 'public', [ConnectorKind.MYSQL]: '', [ConnectorKind.SQLSERVER]: 'dbo' })[kind]

// 種別の選択肢。一覧の表示はサーバーの kindDisplay を使う
export const connectorKindItems = (t: T) => [
  { value: ConnectorKind.POSTGRESQL, title: t('PostgreSQL') },
  { value: ConnectorKind.MYSQL, title: t('MySQL') },
  { value: ConnectorKind.SQLSERVER, title: t('SQL Server') },
]

export const tlsItems = (t: T) => [
  { value: TlsMode.VERIFY, title: t('暗号化して証明書を確かめる') },
  { value: TlsMode.REQUIRE, title: t('暗号化する（証明書を確かめない）') },
  { value: TlsMode.DISABLE, title: t('暗号化しない') },
]

export const connectorOperationItems = (t: T) => [
  {
    value: ConnectorOperation.DATASOURCE_DESCRIBE,
    title: t('表・列の説明を見る'),
    description: 'datasource_describe',
  },
  { value: ConnectorOperation.DATASOURCE_QUERY, title: t('SELECT文で読み取る'), description: 'datasource_query' },
]

export const sensitiveItems = (t: T) => [
  { value: true, title: t('機密') },
  { value: false, title: t('機密でない') },
]

/** 接続先にかかわる操作を待つ秒数（接続のタイムアウト＋実行時間の上限） */
export const connectorWaitSeconds = (connector: Pick<Connector, 'connectTimeoutSeconds' | 'timeoutSeconds'>) =>
  (connector.connectTimeoutSeconds ?? 10) + connector.timeoutSeconds

/** 表・ビューの表示名。MySQLはスキーマを持たない */
export const tableLabel = (table: { schema: string; name: string }) =>
  table.schema ? `${table.schema}.${table.name}` : table.name

const MIB = 1024 * 1024

/** 結果のサイズの上限（バイト）を、入力欄のMB（MiB）にする */
export const bytesToMb = (bytes: number): number => Math.max(1, Math.round(bytes / MIB))

/**
 * 入力欄のMBをバイトにする。入力を変えていなければ元のバイト数を返す（MBで割り切れない値を、保存のたびに丸めて
 * 新しい版を作らないため）
 */
export const mbToBytes = (mb: number, original?: number): number =>
  original !== undefined && bytesToMb(original) === mb ? original : mb * MIB

/** 試し実行の引数（JSONの配列）を読む。空なら引数なし。読めなければエラーの文を返す */
export const parseQueryParams = (text: string, t: T): { value: unknown[]; error: string | null } => {
  if (!text.trim()) return { value: [], error: null }
  try {
    const value: unknown = JSON.parse(text)
    if (Array.isArray(value)) return { value, error: null }
  } catch {
    // 下でエラーの文を返す
  }
  return { value: [], error: t('JSONの配列で書いてください（例：["A01", 10]）') }
}

/**
 * 詳細のタブごとのフォーム（設定・公開する表・利用の許可）を、共有するConnectorに合わせる。別のタブの保存で
 * Connectorが変わったときは、未保存の入力を残して版だけを最新にする（タブごとに送る項目が分かれているので、
 * 残した入力で別のタブの保存を戻さない）。入力を捨てて合わせ直すときは reset() を呼ぶ（保存の後、最新の読み直し）
 */
export const syncConnectorForm = <F extends { version: number }>(
  modelValue: Ref<Connector | null | undefined>,
  form: Ref<F>,
  toForm: (value: Connector | null | undefined) => F,
) => {
  const reset = () => {
    form.value = toForm(modelValue.value)
  }
  watch(
    modelValue,
    (value, old) => {
      const dirty = Boolean(old) && JSON.stringify(form.value) !== JSON.stringify(toForm(old))
      if (value && old && value.id === old.id && dirty) form.value.version = value.version ?? 0
      else form.value = toForm(value)
    },
    { immediate: true },
  )
  return { reset }
}
