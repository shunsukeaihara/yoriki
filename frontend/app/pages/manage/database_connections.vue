<script setup lang="ts">
import type { TableColumn } from '@nuxt/ui'

// 接続先のいずれかの操作ができる人が入れる。一覧は見る（か変更できる）人にだけ出す（APIの ModelPermissions と同じ）
definePageMeta({ permission: ManagePagePermissions.DATABASE_CONNECTION })

const api = useAPI()
const { t } = useI18n()
const { hasPermission, hasAny } = usePermission()
const canView = computed(() =>
  hasAny([PermissionCode.VIEW_DATABASE_CONNECTION, PermissionCode.CHANGE_DATABASE_CONNECTION]),
)
const reloadKey = ref(0)
const reload = () => (reloadKey.value = Date.now())

const emptyParams = (): DatabaseConnectionListParams => ({ name: undefined, kind: undefined, isActive: undefined })
const params = ref(emptyParams())
const clearParams = () => {
  params.value = emptyParams()
  reload()
}

const columns: TableColumn<DatabaseConnection>[] = [
  { accessorKey: 'name', header: t('名前') },
  { accessorKey: 'kindDisplay', header: t('種別') },
  { accessorKey: 'host', header: t('ホスト・DB名') },
  { accessorKey: 'connectorCount', header: t('Connector') },
  { accessorKey: 'isActive', header: t('有効') },
  { accessorKey: 'updated', header: t('更新日時') },
]
</script>

<template>
  <div>
    <UContainer>
      <ListPageTitle
        :title="$t('接続先')"
        :description="
          $t(
            '社内DBのホスト・DB名と資格情報です。エージェントには見せません。1つの接続先の上に、公開する表と使える人の違うConnectorを複数作れます',
          )
        "
      >
        <template #end>
          <!-- 作成はサーバー側でも権限を確認し、なければ403になる -->
          <DatabaseConnectionDialog v-if="hasPermission(PermissionCode.ADD_DATABASE_CONNECTION)" @saved="reload">
            <template #trigger="{ on }">
              <DBtn icon="i-lucide-plus" v-on="on">{{ $t('接続先を追加') }}</DBtn>
            </template>
          </DatabaseConnectionDialog>
        </template>
      </ListPageTitle>

      <p v-if="!canView" class="text-sm text-muted">
        {{ $t('一覧を見るには、接続先を閲覧か変更する権限が要ります') }}
      </p>
      <template v-else>
        <TableSearchForm :reload="reload" :clear="clearParams">
          <DTextField v-model="params.name" :label="$t('名前')" />
          <DSelect v-model="params.kind" :label="$t('種別')" :items="connectorKindItems(t)" clearable />
          <DSelect v-model="params.isActive" :label="$t('有効')" :items="activeItems(t)" clearable />
        </TableSearchForm>

        <DDataTable
          v-model:reload-key="reloadKey"
          :columns="columns"
          :fetch="api.manage.databaseConnections().list"
          :params="params"
        >
          <template #name-cell="{ row }">
            <NuxtLink :to="`/manage/database_connections/${row.original.id}`" class="text-primary">
              {{ row.original.name }}
            </NuxtLink>
          </template>
          <template #host-cell="{ row }">
            <span class="font-mono text-xs">
              {{ row.original.host }}:{{ row.original.port }}/{{ row.original.database }}
            </span>
          </template>
          <template #connectorCount-cell="{ row }">
            <NuxtLink :to="`/manage/connectors?connection=${row.original.id}`" class="text-primary">
              {{ $t('{count}件', { count: row.original.connectorCount }) }}
            </NuxtLink>
          </template>
          <template #isActive-cell="{ row }">
            <UBadge :color="row.original.isActive ? 'success' : 'neutral'" variant="subtle">
              {{ row.original.isActive ? $t('有効') : $t('無効') }}
            </UBadge>
          </template>
          <template #updated-cell="{ row }">{{ formatDatetime(row.original.updated) }}</template>
        </DDataTable>
      </template>
    </UContainer>
    <NuxtPage @changed="reload" />
  </div>
</template>
