<script setup lang="ts">
import type { TableColumn } from '@nuxt/ui'

// 部門・全社・システムのタスクの入口（Webhook）を全件扱う（所属は問わない。authorization.md §5.5）
definePageMeta({ permission: ManagePagePermissions.TRIGGER_BINDING })

const api = useAPI()
const { hasPermission } = usePermission()
const { t } = useI18n()
const reloadKey = ref(0)
const reload = () => (reloadKey.value = Date.now())

const emptyParams = (): TriggerBindingListParams => ({ search: undefined, suspended: undefined })
const params = ref<TriggerBindingListParams>(emptyParams())
const clearParams = () => {
  params.value = emptyParams()
  reload()
}

const columns: TableColumn<TriggerBinding>[] = [
  { accessorKey: 'name', header: t('名前') },
  { accessorKey: 'taskInfo', header: t('起動するタスク') },
  { accessorKey: 'keyPrefix', header: t('鍵') },
  { accessorKey: 'version', header: t('版') },
  { accessorKey: 'suspended', header: t('状態') },
  { accessorKey: 'updated', header: t('更新日時') },
]
</script>

<template>
  <div>
    <UContainer>
      <ListPageTitle
        :title="$t('入口（Webhook）')"
        :description="
          $t(
            '外のサービスからタスクを起動する受け口です。入口とタスクは1対1で、鍵を持つ送り元はそのタスクだけを起動できます。受け取ったpayloadはそのままファイルとしてエージェントに渡し、必要な項目だけを取り出してタスクの入力にします',
          )
        "
      >
        <template #end>
          <!-- 作成はサーバー側でも権限を確認し、なければ403になる -->
          <TriggerBindingDialog v-if="hasPermission(PermissionCode.ADD_TRIGGER_BINDING)" @saved="reload">
            <template #trigger="{ on }">
              <DBtn icon="i-lucide-plus" v-on="on">{{ $t('入口を追加') }}</DBtn>
            </template>
          </TriggerBindingDialog>
        </template>
      </ListPageTitle>

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
        <template #name-cell="{ row }">
          <NuxtLink :to="`/manage/trigger_bindings/${row.original.id}`" class="text-primary">
            {{ row.original.name }}
          </NuxtLink>
        </template>
        <template #taskInfo-cell="{ row }">
          <div>{{ row.original.taskInfo.name }}</div>
          <div class="text-xs text-muted">
            {{ row.original.taskInfo.scopeDisplay }}
            <template v-if="row.original.taskInfo.department">・{{ row.original.taskInfo.department.name }}</template>
            <template v-if="row.original.taskInfo.suspended">・{{ $t('タスクは停止中') }}</template>
          </div>
        </template>
        <template #keyPrefix-cell="{ row }">
          <code class="text-xs text-muted">{{ row.original.keyPrefix }}…</code>
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
