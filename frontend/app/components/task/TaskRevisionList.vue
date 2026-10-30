<script setup lang="ts">
/** タスクの版の履歴。版は保存のたびにでき、Runは受け付けたときの版で動く */
import type { TableColumn } from '@nuxt/ui'

const props = defineProps<{ taskId: string; current?: number }>()
const reloadKey = defineModel<number>('reloadKey', { default: 0 })
const api = useAPI()
const { t } = useI18n()
const params = ref<ListParams>({})

const columns: TableColumn<TaskRevision>[] = [
  { accessorKey: 'number', header: t('版') },
  { accessorKey: 'changeReason', header: t('変更の理由') },
  { accessorKey: 'createdBy', header: t('保存した人') },
  { accessorKey: 'created', header: t('日時') },
]
</script>

<template>
  <DDataTable
    v-model:reload-key="reloadKey"
    :columns="columns"
    :fetch="api.tasks(props.taskId).revisions().list"
    :params="params"
  >
    <template #number-cell="{ row }">
      <div class="flex items-center gap-1.5">
        <span>{{ $t('版 {number}', { number: row.original.number }) }}</span>
        <UBadge v-if="row.original.number === props.current" color="primary" variant="subtle" size="sm">
          {{ $t('今の版') }}
        </UBadge>
      </div>
    </template>
    <template #changeReason-cell="{ row }">
      <p class="max-w-md whitespace-normal text-sm">{{ row.original.changeReason || '—' }}</p>
    </template>
    <template #createdBy-cell="{ row }">
      {{ row.original.createdBy.displayName || row.original.createdBy.email }}
    </template>
    <template #created-cell="{ row }">{{ formatDatetime(row.original.created) }}</template>
  </DDataTable>
</template>
