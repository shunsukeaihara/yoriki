<script setup lang="ts">
/**
 * 入口の受付の履歴（Runの状態まで）。Runの結果・ログは、タスクを管理できる人がタスクの画面で見る（authorization.md
 * §5.5）。重複のキーは送り元の再送を見分けるのに使う
 */
import type { TableColumn } from '@nuxt/ui'

const props = defineProps<{ bindingId: string }>()
const reloadKey = defineModel<number>('reloadKey', { default: 0 })
const api = useAPI()
const { t } = useI18n()
const params = ref<ListParams>({})
const reload = () => (reloadKey.value = Date.now())

const columns: TableColumn<TriggerReceipt>[] = [
  { accessorKey: 'created', header: t('受付') },
  { accessorKey: 'dedupKey', header: t('重複のキー') },
  { accessorKey: 'payloadSize', header: t('大きさ') },
  { accessorKey: 'bindingVersion', header: t('版') },
  { accessorKey: 'runStatus', header: t('Runの状態') },
]
</script>

<template>
  <div class="mb-2 flex justify-end">
    <DBtn color="neutral" variant="ghost" icon="i-lucide-refresh-cw" @click="reload">{{ $t('読み直す') }}</DBtn>
  </div>
  <DDataTable
    v-model:reload-key="reloadKey"
    :columns="columns"
    :fetch="api.manage.triggerBindings(props.bindingId).receipts"
    :params="params"
  >
    <template #created-cell="{ row }">{{ formatDatetime(row.original.created) }}</template>
    <template #dedupKey-cell="{ row }">
      <code v-if="row.original.dedupKey" class="font-mono text-xs break-all">{{ row.original.dedupKey }}</code>
      <span v-else class="text-muted">{{ $t('なし') }}</span>
    </template>
    <template #payloadSize-cell="{ row }">
      <div>{{ formatFileSize(row.original.payloadSize) }}</div>
      <div class="text-xs text-muted">{{ row.original.contentType }}</div>
    </template>
    <template #runStatus-cell="{ row }">
      <UBadge :color="runStatusColor(row.original.runStatus)" variant="subtle" size="sm">
        {{ row.original.runStatusDisplay }}
      </UBadge>
    </template>
  </DDataTable>
</template>
