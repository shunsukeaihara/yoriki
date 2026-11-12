<script setup lang="ts">
/**
 * 予算（design.md §9.1・§9.2）。既定の月の上限（部門・システムの枠。金額・実行回数・同時実行数）と、部門ごとの上書き、
 * 今月の利用額・実行回数を扱う。予算は部門ごととシステムの枠で持ち、個人の予算は持たない
 */
import type { TableColumn } from '@nuxt/ui'

definePageMeta({ permission: ManagePagePermissions.BUDGET })

const { hasPermission } = usePermission()
const api = useAPI()
const { t } = useI18n()
const reloadKey = ref(0)
const reload = () => (reloadKey.value = Date.now())
const canChange = computed(() => hasPermission(PermissionCode.CHANGE_BUDGET))

const { data: settings, error, refresh } = await useAsyncData('budget-settings', () => api.manage.budgetSettings.get())
if (error.value) throw createError(error.value)

const emptyParams = (): ListParams => ({ search: undefined })
const params = ref(emptyParams())
const clearParams = () => {
  params.value = emptyParams()
  reload()
}

const columns: TableColumn<DepartmentBudget>[] = [
  { accessorKey: 'name', header: t('部門') },
  { accessorKey: 'monthlyLimit', header: t('上限の上書き') },
  { accessorKey: 'status', header: t('今月') },
  { id: 'actions', header: '' },
]

/** 部門で上書きしている上限 */
const overrides = (budget: DepartmentBudget): string[] => [
  ...(budget.monthlyLimit === null ? [] : [formatUsd(budget.monthlyLimit, '')]),
  ...(budget.monthlyRuns === null ? [] : [t('月{count}回', { count: budget.monthlyRuns })]),
  ...(budget.concurrentRuns === null ? [] : [t('同時{count}件', { count: budget.concurrentRuns })]),
]

const onSettings = (value: BudgetSettings) => {
  settings.value = value
  reload() // 部門の既定の上限が変わる
}
</script>

<template>
  <div>
    <UContainer>
      <ListPageTitle
        :title="$t('予算')"
        :description="
          $t(
            'LLMの利用額の月の上限です。会話は会話で選んだ部門、タスクはタスクの部門（全社・システムのタスクはシステムの枠）に入ります。上限に達すると、新しい依頼を受け付けず、動いている作業もまとめて止めます。月の実行回数と同時実行数の上限も決められ、達すると新しい依頼を受け付けません（動いている作業は止めません）。月の区切りは組織のタイムゾーンです',
          )
        "
      />

      <div v-if="settings" class="mb-8 grid gap-6 md:grid-cols-2">
        <UCard>
          <template #header>
            <span class="font-semibold">{{ $t('既定の上限') }}</span>
          </template>
          <BudgetSettingsForm :settings="settings" :readonly="!canChange" @updated="onSettings" />
        </UCard>
        <UCard>
          <template #header>
            <span class="font-semibold">{{ $t('システムの枠') }}</span>
          </template>
          <BudgetStatusView :status="settings.system" />
          <DBtn variant="ghost" color="neutral" size="sm" icon="i-lucide-refresh-cw" class="mt-2" @click="refresh()">
            {{ $t('読み直す') }}
          </DBtn>
        </UCard>
      </div>

      <TableSearchForm :reload="reload" :clear="clearParams">
        <DTextField v-model="params.search" :label="$t('部門')" />
      </TableSearchForm>

      <DDataTable
        v-model:reload-key="reloadKey"
        :columns="columns"
        :fetch="api.manage.departmentBudgets().list"
        :params="params"
      >
        <template #monthlyLimit-cell="{ row }">
          <span v-if="!overrides(row.original).length">{{ $t('既定') }}</span>
          <div v-for="text in overrides(row.original)" v-else :key="text">{{ text }}</div>
        </template>
        <template #status-cell="{ row }">
          <BudgetStatusView :status="row.original.status" class="min-w-64" />
        </template>
        <template #actions-cell="{ row }">
          <BudgetDepartmentDialog v-if="canChange" :budget="row.original" @saved="reload">
            <template #trigger="{ on }">
              <DBtn variant="ghost" color="neutral" size="sm" icon="i-lucide-pencil" v-on="on">
                {{ $t('変更') }}
              </DBtn>
            </template>
          </BudgetDepartmentDialog>
        </template>
      </DDataTable>
    </UContainer>
  </div>
</template>
