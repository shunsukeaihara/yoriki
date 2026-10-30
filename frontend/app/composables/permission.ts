/**
 * 画面で使う権限コード（Django の "<app_label>.<codename>"。バックエンドの utils.permissions.AppPermission）。
 * 画面のコードに文字列を直接書かず、使うものをここに追加する。Groupの名前では判定しない
 */
export const PermissionCode = {
  // 基盤の管理（管理画面）。Django標準の view・add・change・delete で、所属は問わない（authorization.md §10）
  VIEW_USER: 'user.view_user',
  ADD_USER: 'user.add_user',
  CHANGE_USER: 'user.change_user',
  VIEW_DEPARTMENT: 'user.view_department',
  ADD_DEPARTMENT: 'user.add_department',
  CHANGE_DEPARTMENT: 'user.change_department',
  DELETE_DEPARTMENT: 'user.delete_department',
  VIEW_SSO_PROVIDER: 'user.view_ssoprovider',
  ADD_SSO_PROVIDER: 'user.add_ssoprovider',
  CHANGE_SSO_PROVIDER: 'user.change_ssoprovider',
  DELETE_SSO_PROVIDER: 'user.delete_ssoprovider',
  VIEW_LLM_CONNECTION: 'llm.view_llmconnection',
  ADD_LLM_CONNECTION: 'llm.add_llmconnection',
  CHANGE_LLM_CONNECTION: 'llm.change_llmconnection',
  DELETE_LLM_CONNECTION: 'llm.delete_llmconnection',
  VIEW_MODEL_PROFILE: 'llm.view_modelprofile',
  ADD_MODEL_PROFILE: 'llm.add_modelprofile',
  CHANGE_MODEL_PROFILE: 'llm.change_modelprofile',
  DELETE_MODEL_PROFILE: 'llm.delete_modelprofile',
  VIEW_RUNTIME_PROFILE: 'runtime.view_runtimeprofile',
  ADD_RUNTIME_PROFILE: 'runtime.add_runtimeprofile',
  CHANGE_RUNTIME_PROFILE: 'runtime.change_runtimeprofile',
  DELETE_RUNTIME_PROFILE: 'runtime.delete_runtimeprofile',
  VIEW_DATABASE_CONNECTION: 'connector.view_databaseconnection',
  ADD_DATABASE_CONNECTION: 'connector.add_databaseconnection',
  CHANGE_DATABASE_CONNECTION: 'connector.change_databaseconnection',
  DELETE_DATABASE_CONNECTION: 'connector.delete_databaseconnection',
  VIEW_CONNECTOR: 'connector.view_connector',
  ADD_CONNECTOR: 'connector.add_connector',
  CHANGE_CONNECTOR: 'connector.change_connector',
  DELETE_CONNECTOR: 'connector.delete_connector',
  // 範囲ごとのSkillの編集・公開（authorization.md §5.1）。部門は管理できる部門（manageableDepartments）にだけ効く
  EDIT_DEPARTMENT_SKILLS: 'skill.can_edit_department_skills',
  PUBLISH_DEPARTMENT_SKILLS: 'skill.can_publish_department_skills',
  EDIT_COMPANY_SKILLS: 'skill.can_edit_company_skills',
  PUBLISH_COMPANY_SKILLS: 'skill.can_publish_company_skills',
  EDIT_SYSTEM_SKILLS: 'skill.can_edit_system_skills',
  PUBLISH_SYSTEM_SKILLS: 'skill.can_publish_system_skills',
  // 範囲ごとのタスクの管理（authorization.md §5.3）。部門は管理できる部門（manageableDepartments）にだけ効く
  MANAGE_DEPARTMENT_TASKS: 'task.can_manage_department_tasks',
  MANAGE_COMPANY_TASKS: 'task.can_manage_company_tasks',
  MANAGE_SYSTEM_TASKS: 'task.can_manage_system_tasks',
} as const
export type PermissionCode = (typeof PermissionCode)[keyof typeof PermissionCode]

/**
 * 画面・メニュー・ボタンの出し分けに使う。表示を隠すだけで、権限はサーバー側で必ず確認している（403）。
 * allPermissions には Groupから得たものも含まれ、基盤管理者はすべてを持つ
 */
export const usePermission = defineStore('permission', () => {
  const account = useAccountStore()
  const permissions = computed(() => account.user?.allPermissions ?? [])
  const hasPermission = (code: PermissionCode) => permissions.value.includes(code)
  const hasAny = (codes: PermissionCode[]) => codes.some(hasPermission)
  return { permissions, hasPermission, hasAny }
})
