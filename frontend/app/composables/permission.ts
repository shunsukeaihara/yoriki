/**
 * 画面で使う権限コード（Django の "<app_label>.<codename>"。バックエンドの utils.permissions.AppPermission）。
 * 画面のコードに文字列を直接書かず、使うものをここに追加する。役割（Group）の名前では判定しない
 */
export const PermissionCode = {
  // ユーザー、SSOの設定、LLMの接続とモデル設定、Runtime Profileは基盤管理者だけが使う。API は PlatformAdmin で守っている
  CHANGE_USER: 'user.change_user',
  CHANGE_SSO_PROVIDER: 'user.change_ssoprovider',
  CHANGE_LLM_CONNECTION: 'llm.change_llmconnection',
  CHANGE_MODEL_PROFILE: 'llm.change_modelprofile',
  CHANGE_RUNTIME_PROFILE: 'runtime.change_runtimeprofile',
} as const
export type PermissionCode = (typeof PermissionCode)[keyof typeof PermissionCode]

/**
 * 画面・メニュー・ボタンの出し分けに使う。表示を隠すだけで、権限はサーバー側で必ず確認している（403）。
 * allPermissions には Group（役割）から得たものも含まれ、基盤管理者はすべてを持つ
 */
export const usePermission = defineStore('permission', () => {
  const account = useAccountStore()
  const permissions = computed(() => account.user?.allPermissions ?? [])
  const hasPermission = (code: PermissionCode) => permissions.value.includes(code)
  const hasAny = (codes: PermissionCode[]) => codes.some(hasPermission)
  return { permissions, hasPermission, hasAny }
})
