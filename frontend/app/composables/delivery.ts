type T = (key: string, params?: Record<string, unknown>) => string

// バックエンドの delivery.models.DestinationKind と同じ値
export const DestinationKind = {
  WEBHOOK: 1,
  ZAPIER: 2,
  IN_APP: 3,
} as const satisfies Record<string, DestinationKindEnum>

/** 送り先のURLを持つ（外へ送る）種類（delivery.models.URL_KINDS） */
export const isUrlDestinationKind = (kind: DestinationKindEnum | null | undefined) =>
  kind === DestinationKind.WEBHOOK || kind === DestinationKind.ZAPIER

/** 種類の選択肢（作成と検索条件）。一覧の表示はサーバーの kindDisplay を使う */
export const destinationKindItems = (t: T) => [
  {
    value: DestinationKind.WEBHOOK,
    title: t('Webhook'),
    description: t('結果を署名付きのJSONで送ります。受け手は署名を確かめます'),
  },
  {
    value: DestinationKind.ZAPIER,
    title: t('Zapier'),
    description: t('ZapierのCatch HookのURLへ送ります'),
  },
  {
    value: DestinationKind.IN_APP,
    title: t('画面の通知'),
    description: t('タスクを管理できる人（手動実行では実行した人）の通知欄に載せます'),
  },
]

// 出口の範囲（delivery.models.DestinationScope。個人の出口はYorikiが作るので、管理では部門・全社・システム）
export const DestinationScope = ConnectorScope

/** 範囲の選択肢（作成と検索条件）。一覧の表示はサーバーの scopeDisplay を使う */
export const destinationScopeItems = (t: T) => [
  { value: DestinationScope.DEPARTMENT, title: t('部門'), description: t('その部門のタスクだけが使います') },
  { value: DestinationScope.COMPANY, title: t('全社'), description: t('全社のタスクが使います') },
  { value: DestinationScope.SYSTEM, title: t('システム'), description: t('システムのタスクだけが使います') },
]

/** 停止の検索条件 */
export const suspendedItems = (t: T) => [
  { value: false, title: t('動いているもの') },
  { value: true, title: t('停止中') },
]

// payloadの大きさの上限（task.models の DEFAULT_MAX_PAYLOAD_BYTES・MAX_PAYLOAD_BYTES。画面ではKiBで扱う）
export const DEFAULT_MAX_PAYLOAD_KIB = 1024
export const MAX_PAYLOAD_KIB = 10 * 1024
export const MIN_PAYLOAD_KIB = 1

// サンプルのpayloadを試したときの、受付がどうするか（task.serializers.TriggerPreviewOutcome）
export const TriggerPreviewOutcome = {
  ACCEPTED: 'accepted',
  REFUSED: 'refused',
  // 必須のファイルの項目があり、JSONのサンプルでは確かめられない
  UNCONFIRMED: 'unconfirmed',
} as const satisfies Record<string, TriggerPreviewOutcomeEnum>

/** 項目の取り出しの1行。名前はタスクの入力の項目から選び、場所はJSONのpathで書く */
export type TriggerInputRow = { name: string | null; path: string }

export const toTriggerInputRows = (inputs: readonly TriggerInput[] | undefined): TriggerInputRow[] =>
  (inputs ?? []).map((i) => ({ name: i.name, path: i.path }))

/** 送る形にする。名前・場所のどちらかが空の行は送らない（書きかけの行で保存を止めない） */
export const fromTriggerInputRows = (rows: readonly TriggerInputRow[]): TriggerInput[] =>
  rows.filter((r) => r.name && r.path.trim()).map((r) => ({ name: r.name!, path: r.path.trim() }))

/** 受け口の絶対URL（サーバーは /api/ からのパスを返す） */
export const hookUrl = (path: string) => (import.meta.client ? new URL(path, window.location.origin).href : path)
