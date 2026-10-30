<script setup lang="ts">
/**
 * 管理できるタスクの一覧（authorization.md §7.3.1）。個人は本人、部門は管理できる部門のタスクの管理者、全社は全社の
 * タスクの管理者。タスクの定義は管理できる人だけに見せる。システムのタスクは管理画面（/manage/system_tasks）で扱う
 */
import type { TableColumn } from '@nuxt/ui'

const api = useAPI()
const { t } = useI18n()
const account = useAccountStore()
const creatable = useTaskCreatableScopes()
const reloadKey = ref(0)
const reload = () => (reloadKey.value = Date.now())

const emptyParams = (): TaskListParams => ({
  name: undefined,
  scope: undefined,
  department: undefined,
  suspended: undefined,
})
const params = ref(emptyParams())
const clearParams = () => {
  params.value = emptyParams()
  reload()
}
// 範囲のタブ（0はすべて）と、部門の範囲の部門（管理できる部門から選ぶ）。選んだらすぐに絞り込む
const ALL = 0
const scopeTabs = computed(() => [
  { value: ALL, title: t('すべて') },
  ...taskScopeItems(t).filter((item) => item.value !== TaskScope.SYSTEM),
])
const scopeTab = computed({
  get: () => params.value.scope ?? ALL,
  set: (value: number) => {
    params.value.scope = value === ALL ? undefined : (value as SkillScopeEnum)
    if (params.value.scope !== TaskScope.DEPARTMENT) params.value.department = undefined
    reload()
  },
})
const departmentItems = computed(() =>
  (account.user?.manageableDepartments ?? []).map((d) => ({ value: d.id, title: d.name })),
)
const suspendedItems = computed(() => [
  { value: false, title: t('動いているもの') },
  { value: true, title: t('停止中') },
])

const columns: TableColumn<Task>[] = [
  { accessorKey: 'name', header: t('名前') },
  { accessorKey: 'scopeDisplay', header: t('範囲') },
  { accessorKey: 'version', header: t('版') },
  { accessorKey: 'description', header: t('説明') },
  { accessorKey: 'triggers', header: t('入口・通知先') },
  { accessorKey: 'updated', header: t('更新日時') },
]

const onCreated = (task: TaskDetail) => navigateTo(`/tasks/${task.id}`)
</script>

<template>
  <UDashboardPanel id="tasks">
    <template #header>
      <UDashboardNavbar :title="$t('タスク')">
        <template #right>
          <TaskCreateDialog v-if="creatable.scopes.value.length" @created="onCreated">
            <template #trigger="{ on }">
              <DBtn icon="i-lucide-plus" v-on="on">{{ $t('タスクを作る') }}</DBtn>
            </template>
          </TaskCreateDialog>
        </template>
      </UDashboardNavbar>
    </template>
    <template #body>
      <UContainer>
        <p class="mb-4 text-sm text-muted">
          {{
            $t(
              '決まった依頼文・Skill・モデルで動く業務です。画面からの手動実行やWebhookで起動し、結果を通知先へ送ります。ここには管理できるタスクだけが出ます',
            )
          }}
        </p>
        <div class="mb-4 flex flex-wrap items-center gap-4">
          <DSelectButton v-model="scopeTab" :label="$t('範囲')" :show-label="false" :items="scopeTabs" />
          <DSelect
            v-if="params.scope === TaskScope.DEPARTMENT"
            v-model="params.department"
            :label="$t('部門')"
            :show-label="false"
            :placeholder="$t('すべての部門')"
            :items="departmentItems"
            clearable
            class="w-56"
            @update:model-value="reload"
          />
        </div>
        <TableSearchForm :reload="reload" :clear="clearParams">
          <DTextField v-model="params.name" :label="$t('名前')" />
          <DSelect v-model="params.suspended" :label="$t('状態')" :items="suspendedItems" clearable />
        </TableSearchForm>

        <DDataTable v-model:reload-key="reloadKey" :columns="columns" :fetch="api.tasks().list" :params="params">
          <template #name-cell="{ row }">
            <div class="flex flex-wrap items-center gap-1.5">
              <NuxtLink :to="`/tasks/${row.original.id}`" class="text-primary">{{ row.original.name }}</NuxtLink>
              <UBadge v-if="row.original.suspended" color="error" variant="subtle" size="sm">{{ $t('停止中') }}</UBadge>
              <UBadge v-if="row.original.handlesSensitiveData" color="warning" variant="subtle" size="sm">
                {{ $t('機密') }}
              </UBadge>
            </div>
          </template>
          <template #scopeDisplay-cell="{ row }">
            <div>{{ row.original.scopeDisplay }}</div>
            <div v-if="row.original.scope !== TaskScope.COMPANY" class="text-xs text-muted">
              {{ taskOwnerLabel(row.original, t) }}
            </div>
          </template>
          <template #version-cell="{ row }">{{ $t('版 {number}', { number: row.original.version }) }}</template>
          <template #description-cell="{ row }">
            <p class="line-clamp-2 max-w-md whitespace-normal text-sm text-muted">{{ row.original.description }}</p>
          </template>
          <template #triggers-cell="{ row }">
            <div class="grid gap-0.5 text-sm">
              <span v-for="trigger in row.original.triggers" :key="trigger.id">
                <UIcon name="i-lucide-webhook" class="align-middle" /> {{ trigger.name }}
              </span>
              <span v-if="row.original.destinationInfo">
                <UIcon name="i-lucide-send" class="align-middle" /> {{ row.original.destinationInfo.name }}
              </span>
            </div>
          </template>
          <template #updated-cell="{ row }">{{ formatDatetime(row.original.updated) }}</template>
        </DDataTable>
      </UContainer>
    </template>
  </UDashboardPanel>
</template>
