<script setup lang="ts">
import type { TableColumn } from '@nuxt/ui'

// 一覧はAPIが所属で絞る。管理画面のページには、作成・編集・削除のいずれかができる人だけが入れる
definePageMeta({ permission: CONNECTOR_EDIT_PERMISSIONS })

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

const columns: TableColumn<Connector>[] = [
  { accessorKey: 'name', header: t('名前') },
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
            '社内DBのうち、エージェントが読み取れる範囲です。接続先の上で公開する表・ビューと使える部門を決め、その部門の社員の依頼で読み取ります（部門を指定しなければ全社向け）。範囲や上限を変えて保存すると新しい版になります',
          )
        "
      >
        <template #end>
          <!-- 作成はサーバー側でも権限と所属を確認し、足りなければ403・400になる -->
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
