<script setup lang="ts">
import type { TableColumn } from '@nuxt/ui'

definePageMeta({ permission: PermissionCode.CHANGE_CONNECTOR })

const api = useAPI()
const router = useRouter()
const { t } = useI18n()
const reloadKey = ref(0)
const reload = () => (reloadKey.value = Date.now())

const emptyParams = (): ConnectorListParams => ({
  name: undefined,
  kind: undefined,
  isActive: undefined,
  isSensitive: undefined,
})
const params = ref(emptyParams())
const clearParams = () => {
  params.value = emptyParams()
  reload()
}

const columns: TableColumn<Connector>[] = [
  { accessorKey: 'name', header: t('名前') },
  { accessorKey: 'kindDisplay', header: t('種別') },
  { accessorKey: 'host', header: t('接続先') },
  { accessorKey: 'tables', header: t('公開する表・ビュー') },
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
            '社内DBへの読み取り専用の接続です。エージェントは、公開した表・ビューだけを、使える役割・部門の社員の依頼で読み取ります。範囲や上限を変えて保存すると新しい版になります',
          )
        "
      >
        <template #end>
          <ConnectorDialog @saved="onCreated">
            <template #trigger="{ on }">
              <DBtn icon="i-lucide-plus" v-on="on">{{ $t('Connectorを追加') }}</DBtn>
            </template>
          </ConnectorDialog>
        </template>
      </ListPageTitle>

      <TableSearchForm :reload="reload" :clear="clearParams">
        <DTextField v-model="params.name" :label="$t('名前')" />
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
        <template #host-cell="{ row }">
          <span class="font-mono text-xs"
            >{{ row.original.host }}:{{ row.original.port }}/{{ row.original.database }}</span
          >
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
