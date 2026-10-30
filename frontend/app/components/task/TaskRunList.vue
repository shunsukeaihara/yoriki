<script setup lang="ts">
/**
 * タスクのRunの履歴（結果を見られるもの。authorization.md §7.1）。状態と終わり方だけを出す。起動のしかた・配信の状態・
 * 通知の再試行・Runの中身は、タスクのRunの画面（まだない）で扱う
 */
import type { TableColumn } from '@nuxt/ui'

const props = defineProps<{ taskId: string }>()
const reloadKey = defineModel<number>('reloadKey', { default: 0 })
const api = useAPI()
const { t } = useI18n()
const params = ref<TaskRunListParams>({})
const reload = () => (reloadKey.value = Date.now())
// 自分が手動実行したRunの状態の変化はログインユーザー宛ての通知で届くので、読み直す（ほかの人・入口からのRunは
// 届かないので、読み直すボタンでも読む）
useUserEvents(
  (event) => {
    if (event.type === 'run.status') reload()
  },
  { onReconnect: reload },
)

const columns: TableColumn<Run>[] = [
  { accessorKey: 'created', header: t('受付') },
  { accessorKey: 'status', header: t('状態') },
  { accessorKey: 'modelProfileName', header: t('モデル') },
  { accessorKey: 'finished', header: t('終了') },
]
</script>

<template>
  <div class="mb-2 flex justify-end">
    <DBtn color="neutral" variant="ghost" icon="i-lucide-refresh-cw" @click="reload">{{ $t('読み直す') }}</DBtn>
  </div>
  <DDataTable
    v-model:reload-key="reloadKey"
    :columns="columns"
    :fetch="api.tasks(props.taskId).runs().list"
    :params="params"
  >
    <template #created-cell="{ row }">{{ formatDatetime(row.original.created) }}</template>
    <template #status-cell="{ row }">
      <div class="flex flex-wrap items-center gap-1.5">
        <UBadge :color="runStatusColor(row.original.status)" variant="subtle" size="sm">
          {{ row.original.statusDisplay }}
        </UBadge>
        <span v-if="row.original.endReasonDisplay" class="text-xs text-muted">
          {{ row.original.endReasonDisplay }}
        </span>
      </div>
    </template>
    <template #finished-cell="{ row }">
      {{ row.original.finished ? formatDatetime(row.original.finished) : '—' }}
    </template>
  </DDataTable>
</template>
