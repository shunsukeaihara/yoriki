<script setup lang="ts">
import type { TableColumn } from '@nuxt/ui'

// 部門・全社・システムの出口を全件扱う（所属は問わない。authorization.md §5.5）。個人のタスクの画面の通知はYorikiが
// 作るので、ここには出ない
definePageMeta({ permission: ManagePagePermissions.DESTINATION })

const api = useAPI()
const { hasPermission } = usePermission()
const { t } = useI18n()
const reloadKey = ref(0)
const reload = () => (reloadKey.value = Date.now())

const emptyParams = (): DestinationListParams => ({
  name: undefined,
  kind: undefined,
  scope: undefined,
  department: undefined,
  suspended: undefined,
})
const params = ref<DestinationListParams>(emptyParams())
const clearParams = () => {
  params.value = emptyParams()
  reload()
}
// 部門の条件は部門の範囲でだけ使う（範囲を変えたら外す。残すと、隠れた条件で一覧が空になる）
watch(
  () => params.value.scope,
  (scope) => {
    if (scope !== DestinationScope.DEPARTMENT) params.value.department = undefined
  },
)

const columns: TableColumn<Destination>[] = [
  { accessorKey: 'name', header: t('名前') },
  { accessorKey: 'kindDisplay', header: t('種類') },
  { accessorKey: 'scopeDisplay', header: t('使ってよい範囲') },
  { accessorKey: 'urlDisplay', header: t('送り先') },
  { accessorKey: 'version', header: t('版') },
  { accessorKey: 'suspended', header: t('状態') },
  { accessorKey: 'updated', header: t('更新日時') },
]
</script>

<template>
  <div>
    <UContainer>
      <ListPageTitle
        :title="$t('出口（通知先）')"
        :description="
          $t(
            'タスクの結果を送る先です。タスクを管理する人は、タスクの範囲で使ってよい出口から選ぶだけで、送り先は変えられません。送り先や機密の扱いを変えて保存すると新しい版になります',
          )
        "
      >
        <template #end>
          <!-- 作成はサーバー側でも権限を確認し、なければ403になる -->
          <DestinationDialog v-if="hasPermission(PermissionCode.ADD_DESTINATION)" @saved="reload">
            <template #trigger="{ on }">
              <DBtn icon="i-lucide-plus" v-on="on">{{ $t('出口を追加') }}</DBtn>
            </template>
          </DestinationDialog>
        </template>
      </ListPageTitle>

      <TableSearchForm :reload="reload" :clear="clearParams">
        <DTextField v-model="params.name" :label="$t('名前')" />
        <DSelect v-model="params.kind" :label="$t('種類')" :items="destinationKindItems(t)" clearable />
        <DSelect v-model="params.scope" :label="$t('範囲')" :items="destinationScopeItems(t)" clearable />
        <DAutocomplete
          v-if="params.scope === DestinationScope.DEPARTMENT"
          v-model="params.department"
          :label="$t('部門')"
          :search-fn="api.manage.destinations().departments"
        />
        <DSelect v-model="params.suspended" :label="$t('状態')" :items="suspendedItems(t)" clearable />
      </TableSearchForm>

      <DDataTable
        v-model:reload-key="reloadKey"
        :columns="columns"
        :fetch="api.manage.destinations().list"
        :params="params"
      >
        <template #name-cell="{ row }">
          <NuxtLink :to="`/manage/destinations/${row.original.id}`" class="text-primary">
            {{ row.original.name }}
          </NuxtLink>
          <UBadge v-if="row.original.allowsSensitiveResults" color="warning" variant="subtle" size="sm" class="ml-2">
            {{ $t('機密可') }}
          </UBadge>
        </template>
        <template #scopeDisplay-cell="{ row }">
          <div>{{ row.original.scopeDisplay }}</div>
          <div v-if="row.original.departmentInfo" class="text-xs text-muted">
            {{ row.original.departmentInfo.name }}
          </div>
        </template>
        <template #urlDisplay-cell="{ row }">
          <span class="font-mono text-xs">{{ row.original.urlDisplay || '-' }}</span>
        </template>
        <template #suspended-cell="{ row }">
          <UBadge :color="row.original.suspended ? 'error' : 'success'" variant="subtle">
            {{ row.original.suspended ? $t('停止中') : $t('動いている') }}
          </UBadge>
        </template>
        <template #updated-cell="{ row }">{{ formatDatetime(row.original.updated) }}</template>
      </DDataTable>
    </UContainer>
    <NuxtPage @changed="reload" />
  </div>
</template>
