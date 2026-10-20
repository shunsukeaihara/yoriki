// バックエンドの user.models.SSOKind と同じ値（django-allauth の provider の ID）
export const SSOKind = {
  MICROSOFT: 'microsoft',
  GOOGLE: 'google',
} as const satisfies Record<string, SSOProviderKindEnum>
export type SSOKind = (typeof SSOKind)[keyof typeof SSOKind]

// 種別の選択肢。一覧の表示はサーバーの kindDisplay を使う
export const ssoKindItems = (t: (key: string) => string) => [
  {
    value: SSOKind.MICROSOFT,
    title: t('Microsoft 365'),
    description: t('Entra IDのテナントを固定して、自社のアカウントだけを受け付けます'),
  },
  {
    value: SSOKind.GOOGLE,
    title: t('Google Workspace'),
    description: t('Workspaceのドメインを確かめて、個人のGoogleアカウントを受け付けません'),
  },
]

/** 認証基盤のアプリに登録するリダイレクトURI。作成前の表示用で、作成後はサーバーの callbackUrl を使う */
export const ssoCallbackUrl = (kind: SSOKind) => `${window.location.origin}/api/accounts/${kind}/login/callback/`

/** 1行に1つ（カンマ区切りも可）で入力したドメインを、API の配列にする */
export const parseDomains = (text: string | null): string[] =>
  (text ?? '')
    .split(/[\s,、]+/)
    .map((d) => d.trim())
    .filter(Boolean)
