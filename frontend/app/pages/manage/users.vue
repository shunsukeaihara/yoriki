<script setup lang="ts">
import type { TableColumn } from '@nuxt/ui'

definePageMeta({ permission: ManagePagePermissions.USER })

const { hasPermission } = usePermission()
const api = useAPI()
const { t } = useI18n()
const reloadKey = ref(0)
const reload = () => (reloadKey.value = Date.now())

const emptyParams = (): ManageUserListParams => ({ search: undefined, isActive: undefined })
const params = ref(emptyParams())
const clearParams = () => {
  params.value = emptyParams()
  reload()
}

const columns: TableColumn<ManageUser>[] = [
  { accessorKey: 'email', header: t('メールアドレス') },
  { accessorKey: 'displayName', header: t('表示名') },
  { accessorKey: 'groups', header: t('Group') },
  { accessorKey: 'ssoProviders', header: t('ログインの方法') },
  { accessorKey: 'isActive', header: t('有効') },
  { accessorKey: 'lastLogin', header: t('最終ログイン') },
]
</script>

<template>
  <div>
    <UContainer>
      <ListPageTitle :title="$t('ユーザー')" :description="$t('社員のアカウントとGroupを管理します')">
        <template #end>
          <ManageUserDialog v-if="hasPermission(PermissionCode.ADD_USER)" @saved="reload">
            <template #trigger="{ on }">
              <DBtn icon="i-lucide-plus" v-on="on">{{ $t('ユーザーを追加') }}</DBtn>
            </template>
          </ManageUserDialog>
        </template>
      </ListPageTitle>

      <TableSearchForm :reload="reload" :clear="clearParams">
        <DTextField v-model="params.search" :label="$t('メールアドレス・表示名')" />
        <DSelect v-model="params.isActive" :label="$t('有効')" :items="activeItems(t)" clearable />
      </TableSearchForm>

      <DDataTable v-model:reload-key="reloadKey" :columns="columns" :fetch="api.manage.users().list" :params="params">
        <template #email-cell="{ row }">
          <NuxtLink :to="`/manage/users/${row.original.id}`" class="text-primary">{{ row.original.email }}</NuxtLink>
          <UBadge v-if="!row.original.canChange" class="ml-2" color="neutral" variant="subtle" size="sm">
            {{ $t('変更できません') }}
          </UBadge>
        </template>
        <template #groups-cell="{ row }">{{ row.original.groups?.join('、') }}</template>
        <template #ssoProviders-cell="{ row }">{{ loginMethodLabel(row.original, t) }}</template>
        <template #isActive-cell="{ row }">
          <UBadge :color="row.original.isActive ? 'success' : 'neutral'" variant="subtle">
            {{ row.original.isActive ? $t('有効') : $t('無効') }}
          </UBadge>
        </template>
        <template #lastLogin-cell="{ row }">{{ formatDatetime(row.original.lastLogin) }}</template>
      </DDataTable>
    </UContainer>
    <NuxtPage @changed="reload" />
  </div>
</template>
