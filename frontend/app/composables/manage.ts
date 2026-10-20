import type { NavigationMenuItem } from '@nuxt/ui'

export type ManageMenuItem = NavigationMenuItem & {
  label: string
  to: string
  /** 項目を表示するのに必要な権限コード。配列はいずれか1つ。リンク先のページの definePageMeta と同じにする */
  permission: PermissionCode | PermissionCode[]
}

/**
 * 管理画面（/manage）のサイドメニュー。権限のない項目は表示しない。
 * 表示できる項目が1つもなければ、/manage には入れない（middleware/manage.ts）。
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
      permission: PermissionCode.CHANGE_LLM_CONNECTION,
    },
    {
      label: $i18n.t('モデル設定'),
      icon: 'i-lucide-cpu',
      to: '/manage/model_profiles',
      permission: PermissionCode.CHANGE_MODEL_PROFILE,
    },
  ])

  const items = computed(() =>
    all.value.filter((item) => hasAny(Array.isArray(item.permission) ? item.permission : [item.permission])),
  )
  return { items }
}
