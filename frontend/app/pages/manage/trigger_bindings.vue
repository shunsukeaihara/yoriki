<script setup lang="ts">
import type { TableColumn } from '@nuxt/ui'

// 発行済みの入口（Webhook）を見渡す（見るだけ。所属は問わない。入口はタスクの画面で作る。authorization.md §5.5）
definePageMeta({ permission: ManagePagePermissions.TRIGGER_BINDING })

const api = useAPI()
const { t } = useI18n()
const reloadKey = ref(0)
const reload = () => (reloadKey.value = Date.now())

const emptyParams = (): TriggerBindingListParams => ({ search: undefined, suspended: undefined })
const params = ref<TriggerBindingListParams>(emptyParams())
const clearParams = () => {
  params.value = emptyParams()
  reload()
}

const userName = (user: MinimumUser | null) => (user ? user.displayName || user.email : '—')

const columns: TableColumn<TriggerBindingListItem>[] = [
  { accessorKey: 'name', header: t('名前') },
  { accessorKey: 'taskInfo', header: t('起動するタスク') },
  { accessorKey: 'keyPrefix', header: t('鍵') },
  { accessorKey: 'suspended', header: t('状態') },
  { accessorKey: 'created', header: t('作成') },
  { accessorKey: 'keyRotated', header: t('鍵の作成') },
]
</script>

<template>
  <div>
    <UContainer>
      <ListPageTitle
        :title="$t('入口（Webhook）')"
        :description="
          $t(
            '発行済みの入口の一覧です。入口は、そのタスクを管理できる人がタスクの画面で作り、設定します。鍵を作った人がタスクを管理できなくなった入口には警告を出します',
          )
        "
      />

      <TableSearchForm :reload="reload" :clear="clearParams">
        <DTextField v-model="params.search" :label="$t('名前・タスクの名前')" />
        <DSelect v-model="params.suspended" :label="$t('状態')" :items="suspendedItems(t)" clearable />
      </TableSearchForm>

      <DDataTable
        v-model:reload-key="reloadKey"
        :columns="columns"
        :fetch="api.manage.triggerBindings().list"
        :params="params"
      >
        <template #taskInfo-cell="{ row }">
          <div>{{ row.original.taskInfo.name }}</div>
          <div class="text-xs text-muted">
            {{ row.original.taskInfo.scopeDisplay }}
            <template v-if="row.original.taskInfo.department">・{{ row.original.taskInfo.department.name }}</template>
          </div>
        </template>
        <template #keyPrefix-cell="{ row }">
          <code class="text-xs text-muted">{{ row.original.keyPrefix }}…</code>
          <UBadge v-if="row.original.keyNeedsRotation" color="warning" variant="subtle" size="sm" class="ml-2">
            {{ $t('鍵の確認') }}
          </UBadge>
        </template>
        <template #suspended-cell="{ row }">
          <UBadge :color="row.original.suspended ? 'error' : 'success'" variant="subtle">
            {{ row.original.suspended ? $t('停止中') : $t('動いている') }}
          </UBadge>
        </template>
        <template #created-cell="{ row }">
          <div>{{ formatDatetime(row.original.created) }}</div>
          <div class="text-xs text-muted">{{ userName(row.original.createdBy) }}</div>
        </template>
        <template #keyRotated-cell="{ row }">
          <div>{{ row.original.keyRotated ? formatDatetime(row.original.keyRotated) : '—' }}</div>
          <div class="text-xs text-muted">{{ userName(row.original.keyCreatedBy) }}</div>
        </template>
      </DDataTable>
    </UContainer>
  </div>
</template>
