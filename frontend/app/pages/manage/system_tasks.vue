<script setup lang="ts">
/**
 * システムのタスク（authorization.md §5.3）。システムのSkillを使い、業務画面の一覧には出さない。中身の編集・実行は、
 * 業務画面と同じタスクの詳細（/tasks/[taskId]）で行う
 */
import type { TableColumn } from '@nuxt/ui'

definePageMeta({ permission: ManagePagePermissions.SYSTEM_TASK })

const api = useAPI()
const { t } = useI18n()
const reloadKey = ref(0)
const reload = () => (reloadKey.value = Date.now())

const emptyParams = (): TaskListParams => ({ scope: TaskScope.SYSTEM, name: undefined })
const params = ref(emptyParams())
const clearParams = () => {
  params.value = emptyParams()
  reload()
}

const columns: TableColumn<Task>[] = [
  { accessorKey: 'name', header: t('名前') },
  { accessorKey: 'version', header: t('版') },
  { accessorKey: 'description', header: t('説明') },
  { accessorKey: 'updated', header: t('更新日時') },
]

const onCreated = (task: TaskDetail) => navigateTo(`/tasks/${task.id}`)
</script>

<template>
  <UContainer>
    <ListPageTitle
      :title="$t('システムのタスク')"
      :description="
        $t('システムのSkillを使うタスクです。業務画面の一覧には出しません。中身はタスクの画面で編集・実行します')
      "
    >
      <template #end>
        <TaskCreateDialog :scopes="[TaskScope.SYSTEM]" @created="onCreated">
          <template #trigger="{ on }">
            <DBtn icon="i-lucide-plus" v-on="on">{{ $t('タスクを作る') }}</DBtn>
          </template>
        </TaskCreateDialog>
      </template>
    </ListPageTitle>

    <TableSearchForm :reload="reload" :clear="clearParams">
      <DTextField v-model="params.name" :label="$t('名前')" />
    </TableSearchForm>

    <DDataTable v-model:reload-key="reloadKey" :columns="columns" :fetch="api.tasks().list" :params="params">
      <template #name-cell="{ row }">
        <div class="flex flex-wrap items-center gap-1.5">
          <NuxtLink :to="`/tasks/${row.original.id}`" class="text-primary">{{ row.original.name }}</NuxtLink>
          <UBadge v-if="row.original.suspended" color="error" variant="subtle" size="sm">{{ $t('停止中') }}</UBadge>
        </div>
      </template>
      <template #version-cell="{ row }">{{ $t('版 {number}', { number: row.original.version }) }}</template>
      <template #description-cell="{ row }">
        <p class="line-clamp-2 max-w-md whitespace-normal text-sm text-muted">{{ row.original.description }}</p>
      </template>
      <template #updated-cell="{ row }">{{ formatDatetime(row.original.updated) }}</template>
    </DDataTable>
  </UContainer>
</template>
