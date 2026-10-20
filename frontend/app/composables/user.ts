// バックエンドの user.models.InvitationPurpose と同じ値
export const InvitationPurpose = {
  INVITE: 1,
  RESET: 2,
} as const

/** 一覧に出すログインの方法。SSO の認証基盤の名前は SSO の設定の種別の表示名と同じにする */
export const loginMethodLabel = (user: ManageUser, t: (key: string) => string): string => {
  const names: Record<string, string> = { microsoft: t('Microsoft 365'), google: t('Google Workspace') }
  if (user.ssoProviders.length) return user.ssoProviders.map((p) => names[p] ?? p).join('、')
  return user.hasPassword ? t('パスワード') : t('未設定')
}
