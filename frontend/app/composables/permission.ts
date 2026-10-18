/** 画面で使う権限コード（Django の "<app_label>.<codename>"）。画面のコードに文字列を直接書かない */
export const PermissionCode = {
  VIEW_USER: 'user.view_user',
} as const
export type PermissionCode = (typeof PermissionCode)[keyof typeof PermissionCode]

/** 役割。基盤管理者は業務管理者・Plugin公開者の操作もできる（バックエンドの utils/permissions.py と同じ） */
export const Role = {
  BUSINESS_ADMIN: 'businessAdmin',
  PLUGIN_PUBLISHER: 'pluginPublisher',
  PLATFORM_ADMIN: 'platformAdmin',
} as const
export type Role = (typeof Role)[keyof typeof Role]

/**
 * 画面・メニュー・ボタンの出し分けに使う。表示を隠すだけで、権限はサーバー側で必ず確認している（403）
 */
export const usePermission = defineStore('permission', () => {
  const account = useAccountStore()
  const permissions = computed(() => account.user?.allPermissions ?? [])
  const hasPermission = (code: PermissionCode) => permissions.value.includes(code)
  const hasAny = (codes: PermissionCode[]) => codes.some(hasPermission)

  const hasRole = (role: Role) => {
    const user = account.user
    if (!user) return false
    if (user.isPlatformAdmin) return true
    if (role === Role.BUSINESS_ADMIN) return user.isBusinessAdmin
    if (role === Role.PLUGIN_PUBLISHER) return user.isPluginPublisher
    return false
  }
  const hasAnyRole = (roles: Role[]) => roles.some(hasRole)

  return { permissions, hasPermission, hasAny, hasRole, hasAnyRole }
})
