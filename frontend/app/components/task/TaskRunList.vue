<script setup lang="ts">
/**
 * タスクのRunの履歴（結果とログを見られるもの。authorization.md §7.1）。起動のしかた、処理の状態、通知の配信の状態を
 * 並べ、受付の日時からRunの画面（中身・配信の詳細・通知の再試行）へ移る
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

const columns: TableColumn<TaskRun>[] = [
  { accessorKey: 'created', header: t('受付') },
  { accessorKey: 'launch', header: t('起動') },
  { accessorKey: 'status', header: t('状態') },
  { accessorKey: 'deliveriesInfo', header: t('通知') },
  { accessorKey: 'modelProfileName', header: t('モデル') },
  { accessorKey: 'finished', header: t('終了') },
]

const launchedBy = (run: TaskRun) =>
  run.startedBy ? run.startedBy.displayName || run.startedBy.email : (run.receipt?.bindingName ?? '')
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
    <template #created-cell="{ row }">
      <NuxtLink :to="`/tasks/${props.taskId}/runs/${row.original.id}`" class="text-primary">
        {{ formatDatetime(row.original.created) }}
      </NuxtLink>
    </template>
    <template #launch-cell="{ row }">
      <div class="flex flex-col">
        <span class="text-sm">{{ row.original.launchDisplay }}</span>
        <span v-if="launchedBy(row.original)" class="text-xs text-muted">{{ launchedBy(row.original) }}</span>
      </div>
    </template>
    <template #status-cell="{ row }">
      <div class="flex flex-wrap items-center gap-1.5">
        <UBadge :color="runStatusColor(row.original.status)" variant="subtle" size="sm">
          {{ row.original.statusDisplay }}
        </UBadge>
        <!-- 完了のときの終わり方（完了）は状態と同じなので出さない -->
        <span
          v-if="row.original.endReasonDisplay && row.original.endReasonDisplay !== row.original.statusDisplay"
          class="text-xs text-muted"
        >
          {{ row.original.endReasonDisplay }}
        </span>
      </div>
    </template>
    <template #deliveriesInfo-cell="{ row }">
      <ul v-if="row.original.deliveriesInfo.length" class="grid gap-0.5">
        <li v-for="delivery in row.original.deliveriesInfo" :key="delivery.id" class="flex items-center gap-1.5">
          <UBadge :color="deliveryStatusColor(delivery.status)" variant="subtle" size="sm">
            {{ delivery.statusDisplay }}
          </UBadge>
          <span class="truncate text-xs text-muted">{{ delivery.destinationName }}</span>
        </li>
      </ul>
      <span v-else class="text-dimmed">—</span>
    </template>
    <template #finished-cell="{ row }">
      {{ row.original.finished ? formatDatetime(row.original.finished) : '—' }}
    </template>
  </DDataTable>
</template>
