<script setup lang="ts">
import type { TableColumn } from '@nuxt/ui'

definePageMeta({ permission: ManagePagePermissions.SSO_PROVIDER })

const { hasPermission } = usePermission()
const api = useAPI()
const { t } = useI18n()
const reloadKey = ref(0)
const reload = () => (reloadKey.value = Date.now())
const params = ref({})

const columns: TableColumn<SSOProvider>[] = [
  { accessorKey: 'kindDisplay', header: t('種別') },
  { accessorKey: 'clientId', header: t('client ID') },
  { accessorKey: 'allowedEmailDomains', header: t('許可するドメイン') },
  { accessorKey: 'isActive', header: t('有効') },
  { accessorKey: 'updated', header: t('更新日時') },
]
</script>

<template>
  <div>
    <UContainer>
      <ListPageTitle
        :title="$t('SSOの設定')"
        :description="$t('社員がログインに使う社内SSOの認証基盤を登録します。種別ごとに1つ登録できます')"
      >
        <template #end>
          <SsoProviderDialog v-if="hasPermission(PermissionCode.ADD_SSO_PROVIDER)" @saved="reload">
            <template #trigger="{ on }">
              <DBtn icon="i-lucide-plus" v-on="on">{{ $t('SSOの設定を追加') }}</DBtn>
            </template>
          </SsoProviderDialog>
        </template>
      </ListPageTitle>

      <DDataTable
        v-model:reload-key="reloadKey"
        :columns="columns"
        :fetch="api.manage.ssoProviders().list"
        :params="params"
      >
        <template #kindDisplay-cell="{ row }">
          <NuxtLink :to="`/manage/sso_providers/${row.original.id}`" class="text-primary">
            {{ row.original.kindDisplay }}
          </NuxtLink>
        </template>
        <template #clientId-cell="{ row }">
          <span class="font-mono">{{ row.original.clientId }}</span>
        </template>
        <template #allowedEmailDomains-cell="{ row }">{{ row.original.allowedEmailDomains.join('、') }}</template>
        <template #isActive-cell="{ row }">
          <UBadge :color="row.original.isActive ? 'success' : 'neutral'" variant="subtle">
            {{ row.original.isActive ? $t('有効') : $t('無効') }}
          </UBadge>
        </template>
        <template #updated-cell="{ row }">{{ formatDatetime(row.original.updated) }}</template>
      </DDataTable>
    </UContainer>
    <NuxtPage @changed="reload" />
  </div>
</template>
