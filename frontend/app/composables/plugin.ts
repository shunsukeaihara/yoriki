type T = (key: string) => string

// 本体のPluginのAdapterのID（api/src/plugin/builtin.py と同じ値。design.md §3.11.1）
export const BuiltinExtension = {
  WEBHOOK_TRIGGER: 'yoriki.trigger.webhook',
  WEBHOOK: 'yoriki.notification.webhook',
  ZAPIER: 'yoriki.notification.zapier',
  IN_APP: 'yoriki.notification.in_app',
  POSTGRESQL: 'yoriki.connector.postgresql',
  MYSQL: 'yoriki.connector.mysql',
  SQLSERVER: 'yoriki.connector.sqlserver',
  SLACK: 'yoriki.channel.slack',
  DISCORD: 'yoriki.channel.discord',
} as const

// 対応能力の名前（api/src/plugin/builtin.py。意味は plugin/CONTRACT.md）
export const Capability = {
  // 出口：外へ送る（宛先のURLを持つ）
  URL: 'url',
  // Connector：表の名前にスキーマがある
  SCHEMAS: 'schemas',
} as const

/** 本体の種別の説明（自社のPluginは名前だけを出す） */
const builtinDescriptions = (t: T): Record<string, string> => ({
  [BuiltinExtension.WEBHOOK]: t('結果を署名付きのJSONで送ります。受け手は署名を確かめます'),
  [BuiltinExtension.ZAPIER]: t('ZapierのCatch HookのURLへ送ります'),
  [BuiltinExtension.IN_APP]: t('タスクを管理できる人（手動実行では実行した人）の通知欄に載せます'),
  [BuiltinExtension.SLACK]: t(
    'Slackのチャネルのスレッドで作業中の質問に答えてもらい、完了を報告します。タスクの対話の出口にだけ選べます',
  ),
  [BuiltinExtension.DISCORD]: t(
    'Discordのテキストチャネルのスレッドで作業中の質問に答えてもらい、完了を報告します。返信の受信にはchannel-listenerが要ります',
  ),
})

/** 選べる種別を選択肢にする */
export const extensionItems = (extensions: SelectableExtension[] | null | undefined, t: T) =>
  (extensions ?? []).map((e) => ({ value: e.id, title: e.name, description: builtinDescriptions(t)[e.id] }))

export const hasCapability = (extension: SelectableExtension | null | undefined, capability: string) =>
  Boolean(extension?.capabilities.includes(capability))

/** 種別の設定のSchemaの既定値（例：接続先のポート） */
export const configDefault = (extension: SelectableExtension | null | undefined, key: string): unknown => {
  const properties = (extension?.configSchema as { properties?: Record<string, { default?: unknown }> } | undefined)
    ?.properties
  return properties?.[key]?.default
}

/** 新しく作る入口・出口・Connectorの接続で選べる種別（使えて、廃止予定でないPluginのAdapter） */
export const useSelectableExtensions = (type: ExtensionType) => {
  const api = useAPI()
  const { data, status } = useAsyncData(`plugin-extensions-${type}`, () => api.pluginExtensions.list(type), {
    default: () => [] as SelectableExtension[],
  })
  const find = (id: string | null | undefined) => data.value.find((e) => e.id === id)
  return { extensions: data, status, find }
}
