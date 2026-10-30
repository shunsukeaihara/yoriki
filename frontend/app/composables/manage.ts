import type { NavigationMenuItem } from '@nuxt/ui'

/** ページへのリンク */
export type ManageMenuLink = NavigationMenuItem & {
  label: string
  to: string
  /** 項目を表示するのに必要な権限コード。配列はいずれか1つ。リンク先のページの definePageMeta と同じにする */
  permission: PermissionCode | PermissionCode[]
}
/** 項目をまとめる見出し。表示できる子が1つもなければ出さない */
export type ManageMenuGroup = NavigationMenuItem & { label: string; children: ManageMenuItem[] }
export type ManageMenuItem = ManageMenuLink | ManageMenuGroup

/** 管理画面のページに入れるpermission（見るか変えるか。authorization.md §10）。リンク先の definePageMeta と同じにする */
export const ManagePagePermissions = {
  LLM_CONNECTION: [PermissionCode.VIEW_LLM_CONNECTION, PermissionCode.CHANGE_LLM_CONNECTION],
  MODEL_PROFILE: [PermissionCode.VIEW_MODEL_PROFILE, PermissionCode.CHANGE_MODEL_PROFILE],
  RUNTIME_PROFILE: [PermissionCode.VIEW_RUNTIME_PROFILE, PermissionCode.CHANGE_RUNTIME_PROFILE],
  DATABASE_CONNECTION: [PermissionCode.VIEW_DATABASE_CONNECTION, PermissionCode.CHANGE_DATABASE_CONNECTION],
  CONNECTOR: [PermissionCode.VIEW_CONNECTOR, PermissionCode.CHANGE_CONNECTOR],
  SYSTEM_SKILL: [PermissionCode.EDIT_SYSTEM_SKILLS, PermissionCode.PUBLISH_SYSTEM_SKILLS],
  SYSTEM_TASK: [PermissionCode.MANAGE_SYSTEM_TASKS],
  USER: [PermissionCode.VIEW_USER, PermissionCode.CHANGE_USER],
  DEPARTMENT: [PermissionCode.VIEW_DEPARTMENT, PermissionCode.CHANGE_DEPARTMENT],
  SSO_PROVIDER: [PermissionCode.VIEW_SSO_PROVIDER, PermissionCode.CHANGE_SSO_PROVIDER],
} satisfies Record<string, PermissionCode[]>

const isGroup = (item: ManageMenuItem): item is ManageMenuGroup => 'children' in item && Array.isArray(item.children)

/**
 * 管理画面（/manage）のサイドメニュー。見出しで入れ子にでき、権限のない項目は表示しない。
 * 表示できるリンクが1つもなければ、/manage には入れない（middleware/manage.ts）。
 * middleware からも呼ぶので、label は useI18n() ではなく useNuxtApp().$i18n.t で翻訳する
 */
export const useManageMenu = () => {
  const { hasAny } = usePermission()
  const { $i18n } = useNuxtApp()

  const all = computed<ManageMenuItem[]>(() => [
    {
      label: $i18n.t('LLMの接続'),
      icon: 'i-lucide-plug',
      to: '/manage/llm_connections',
      permission: ManagePagePermissions.LLM_CONNECTION,
    },
    {
      label: $i18n.t('モデル設定'),
      icon: 'i-lucide-cpu',
      to: '/manage/model_profiles',
      permission: ManagePagePermissions.MODEL_PROFILE,
    },
    {
      label: $i18n.t('Runtime Profile'),
      icon: 'i-lucide-box',
      to: '/manage/runtime_profiles',
      permission: ManagePagePermissions.RUNTIME_PROFILE,
    },
    {
      label: $i18n.t('Connector'),
      icon: 'i-lucide-cable',
      defaultOpen: true,
      children: [
        {
          label: $i18n.t('DB Connector'),
          icon: 'i-lucide-database',
          defaultOpen: true,
          children: [
            {
              label: $i18n.t('接続先'),
              icon: 'i-lucide-server',
              to: '/manage/database_connections',
              permission: ManagePagePermissions.DATABASE_CONNECTION,
            },
            {
              label: $i18n.t('Connector'),
              icon: 'i-lucide-table-2',
              to: '/manage/connectors',
              // 部門・全社・システムのConnectorを全件扱う（所属は問わない）
              permission: ManagePagePermissions.CONNECTOR,
            },
          ],
        },
      ],
    },
    {
      label: $i18n.t('システムのSkill'),
      icon: 'i-lucide-book-lock',
      to: '/manage/system_skills',
      permission: ManagePagePermissions.SYSTEM_SKILL,
    },
    {
      label: $i18n.t('システムのタスク'),
      icon: 'i-lucide-workflow',
      to: '/manage/system_tasks',
      permission: ManagePagePermissions.SYSTEM_TASK,
    },
    {
      label: $i18n.t('ユーザー'),
      icon: 'i-lucide-users',
      to: '/manage/users',
      permission: ManagePagePermissions.USER,
    },
    {
      label: $i18n.t('部門'),
      icon: 'i-lucide-building-2',
      to: '/manage/departments',
      permission: ManagePagePermissions.DEPARTMENT,
    },
    {
      label: $i18n.t('SSOの設定'),
      icon: 'i-lucide-key-round',
      to: '/manage/sso_providers',
      permission: ManagePagePermissions.SSO_PROVIDER,
    },
  ])

  const visible = (items: ManageMenuItem[]): ManageMenuItem[] =>
    items.flatMap((item): ManageMenuItem[] => {
      if (isGroup(item)) {
        const children = visible(item.children)
        return children.length ? [{ ...item, children }] : []
      }
      const { permission } = item as ManageMenuLink
      return hasAny(Array.isArray(permission) ? permission : [permission]) ? [item] : []
    })
  const flatten = (items: ManageMenuItem[]): ManageMenuLink[] =>
    items.flatMap((item): ManageMenuLink[] => (isGroup(item) ? flatten(item.children) : [item as ManageMenuLink]))

  /** サイドメニューに出す木 */
  const items = computed(() => visible(all.value))
  /** 表示できるリンク（メニューの順）。見出し・最初の移動先・入口の判定に使う */
  const links = computed(() => flatten(items.value))
  return { items, links }
}
