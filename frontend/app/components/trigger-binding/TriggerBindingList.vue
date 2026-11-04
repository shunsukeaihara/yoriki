<script setup lang="ts">
/**
 * タスクの入口（Webhook）の一覧（タスクの画面の「入口」のタブ。authorization.md §5.5）。1つのタスクに、送り元ごとに複数の
 * 入口を付けられる。名前から入口の画面（受け取り方・試す・受付の履歴・版の履歴・鍵）へ移る
 */
import type { TableColumn } from '@nuxt/ui'

const props = defineProps<{ task: TaskDetail }>()
const emit = defineEmits<{ changed: [] }>()
const reloadKey = defineModel<number>('reloadKey', { default: 0 })
const api = useAPI()
const { t } = useI18n()
const params = ref<ListParams>({})

const onSaved = () => {
  reloadKey.value = Date.now()
  emit('changed')
}

const columns: TableColumn<TriggerBinding>[] = [
  { accessorKey: 'name', header: t('名前') },
  { accessorKey: 'keyPrefix', header: t('鍵') },
  { accessorKey: 'version', header: t('版') },
  { accessorKey: 'suspended', header: t('状態') },
  { accessorKey: 'updated', header: t('更新日時') },
]
</script>

<template>
  <div class="mb-2 flex justify-end">
    <TriggerBindingDialog :task="props.task" @saved="onSaved">
      <template #trigger="{ on }">
        <DBtn icon="i-lucide-plus" v-on="on">{{ $t('入口を追加') }}</DBtn>
      </template>
    </TriggerBindingDialog>
  </div>
  <DDataTable
    v-model:reload-key="reloadKey"
    :columns="columns"
    :fetch="api.tasks(props.task.id).triggers().list"
    :params="params"
  >
    <template #name-cell="{ row }">
      <NuxtLink :to="`/tasks/${props.task.id}/triggers/${row.original.id}`" class="text-primary">
        {{ row.original.name }}
      </NuxtLink>
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
    <template #updated-cell="{ row }">{{ formatDatetime(row.original.updated) }}</template>
  </DDataTable>
</template>
