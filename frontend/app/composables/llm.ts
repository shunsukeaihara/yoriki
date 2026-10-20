// バックエンドの llm.models.ProviderKind と同じ値
export const ProviderKind = {
  OPENAI: 1,
  ANTHROPIC: 2,
  GEMINI: 3,
  LOCAL: 4,
} as const satisfies Record<string, ProviderKindEnum>
export type ProviderKind = (typeof ProviderKind)[keyof typeof ProviderKind]

// クラウドサービスの接続先は固定する。base URL を指定するのはローカルLLMだけ（ProviderKind.uses_base_url）
export const usesBaseUrl = (kind: ProviderKind) => kind === ProviderKind.LOCAL
// ローカルLLMは、API key なしで動くものがある（ProviderKind.requires_api_key）
export const requiresApiKey = (kind: ProviderKind) => kind !== ProviderKind.LOCAL

// 種別の選択肢。一覧の表示はサーバーの kindDisplay を使う
export const providerKindItems = (t: (key: string) => string) => [
  { value: ProviderKind.OPENAI, title: t('OpenAI') },
  { value: ProviderKind.ANTHROPIC, title: t('Anthropic（Claude）') },
  { value: ProviderKind.GEMINI, title: t('Google Gemini') },
  {
    value: ProviderKind.LOCAL,
    title: t('ローカルLLM'),
    description: t('社内で動かすLLM（vLLM、Ollama等）。OpenAI互換のAPIで呼びます'),
  },
]

export const activeItems = (t: (key: string) => string) => [
  { value: true, title: t('有効') },
  { value: false, title: t('無効') },
]

/** 単価（100万tokenあたりの米ドル。DecimalField の文字列）を表示する。計算には使わない */
export const formatPrice = (value: string | null | undefined): string => {
  if (value === null || value === undefined || value === '') return ''
  const { $i18n } = useNuxtApp()
  return new Intl.NumberFormat($i18n.locale.value, {
    style: 'currency',
    currency: 'USD',
    maximumFractionDigits: 6,
  }).format(Number(value))
}
