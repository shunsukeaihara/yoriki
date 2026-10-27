<script setup lang="ts">
import type { TableColumn } from '@nuxt/ui'

// 部門・全社・システムのConnectorを全件扱う（所属は問わない。authorization.md §5.2）
definePageMeta({ permission: ManagePagePermissions.CONNECTOR })

const api = useAPI()
const { hasPermission } = usePermission()
const route = useRoute()
const router = useRouter()
const { t } = useI18n()
const reloadKey = ref(0)
const reload = () => (reloadKey.value = Date.now())

const emptyParams = (): ConnectorListParams => ({
  name: undefined,
  connection: undefined,
  kind: undefined,
  isActive: undefined,
  isSensitive: undefined,
  scope: undefined,
  department: undefined,
})
// DB接続の一覧から「Connector」の件数を押して開いたときは、そのDB接続で絞り込む
const params = ref<ConnectorListParams>({
  ...emptyParams(),
  connection: (route.query.connection as string | undefined) || undefined,
})
// 絞り込みの欄に、URLで渡されたDB接続の名前を出す（見つからなければIDのまま）
const { data: initialConnection } = await useAsyncData('connectors-initial-connection', () =>
  params.value.connection
    ? api.manage
        .connectors()
        .connections({ pageSize: 1000 })
        .then((page) => page.results.find((c) => c.id === params.value.connection) ?? null)
        .catch(() => null)
    : Promise.resolve(null),
)
const clearParams = () => {
  params.value = emptyParams()
  reload()
}
// 部門の条件は部門の範囲でだけ使う（範囲を変えたら外す。残すと、隠れた条件で一覧が空になる）
watch(
  () => params.value.scope,
  (scope) => {
    if (scope !== ConnectorScope.DEPARTMENT) params.value.department = undefined
  },
)

const columns: TableColumn<Connector>[] = [
  { accessorKey: 'name', header: t('名前') },
  { accessorKey: 'scopeDisplay', header: t('範囲') },
  { accessorKey: 'connection', header: t('接続先') },
  { accessorKey: 'kindDisplay', header: t('種別') },
  { accessorKey: 'tables', header: t('公開する表') },
  { accessorKey: 'version', header: t('版') },
  { accessorKey: 'isActive', header: t('有効') },
  { accessorKey: 'updated', header: t('更新日時') },
]

// 作成したら、公開する表を選べるよう詳細を開く
const onCreated = (connector: Connector) => {
  reload()
  router.push(`/manage/connectors/${connector.id}`)
}
</script>

<template>
  <div>
    <UContainer>
      <ListPageTitle
        :title="$t('Connector')"
        :description="
          $t(
            '社内DBのうち、エージェントが読み取れる範囲です。接続先の上で公開する表・ビューを決めます。部門のConnectorはその部門の社員が、全社のConnectorは全社員が会話で使い、システムのConnectorはシステムのタスクだけが使います。表や上限を変えて保存すると新しい版になります',
          )
        "
      >
        <template #end>
          <!-- 作成はサーバー側でも権限を確認し、なければ403になる -->
          <ConnectorDialog v-if="hasPermission(PermissionCode.ADD_CONNECTOR)" @saved="onCreated">
            <template #trigger="{ on }">
              <DBtn icon="i-lucide-plus" v-on="on">{{ $t('Connectorを追加') }}</DBtn>
            </template>
          </ConnectorDialog>
        </template>
      </ListPageTitle>

      <TableSearchForm :reload="reload" :clear="clearParams">
        <DTextField v-model="params.name" :label="$t('名前')" />
        <DAutocomplete
          v-model="params.connection"
          :label="$t('接続先')"
          :search-fn="api.manage.connectors().connections"
          item-description="kindDisplay"
          :initial-selected="initialConnection"
        />
        <DSelect v-model="params.scope" :label="$t('範囲')" :items="connectorScopeItems(t)" clearable />
        <DAutocomplete
          v-if="params.scope === ConnectorScope.DEPARTMENT"
          v-model="params.department"
          :label="$t('部門')"
          :search-fn="api.manage.connectors().departments"
        />
        <DSelect v-model="params.kind" :label="$t('種別')" :items="connectorKindItems(t)" clearable />
        <DSelect v-model="params.isSensitive" :label="$t('機密')" :items="sensitiveItems(t)" clearable />
        <DSelect v-model="params.isActive" :label="$t('有効')" :items="activeItems(t)" clearable />
      </TableSearchForm>

      <DDataTable
        v-model:reload-key="reloadKey"
        :columns="columns"
        :fetch="api.manage.connectors().list"
        :params="params"
      >
        <template #name-cell="{ row }">
          <NuxtLink :to="`/manage/connectors/${row.original.id}`" class="text-primary">
            {{ row.original.name }}
          </NuxtLink>
          <UBadge v-if="row.original.isSensitive" color="warning" variant="subtle" size="sm" class="ml-2">
            {{ $t('機密') }}
          </UBadge>
        </template>
        <template #scopeDisplay-cell="{ row }">
          <div>{{ row.original.scopeDisplay }}</div>
          <div v-if="row.original.departmentInfo" class="text-xs text-muted">
            {{ row.original.departmentInfo.name }}
          </div>
        </template>
        <template #connection-cell="{ row }">
          <NuxtLink
            v-if="row.original.connectionInfo.host"
            :to="`/manage/database_connections/${row.original.connection}`"
            class="text-primary"
          >
            {{ row.original.connectionInfo.name }}
          </NuxtLink>
          <span v-else>{{ row.original.connectionInfo.name }}</span>
        </template>
        <template #tables-cell="{ row }">
          {{ $t('{count}件', { count: row.original.tables?.length ?? 0 }) }}
        </template>
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
