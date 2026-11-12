<script setup lang="ts">
/**
 * 今月の予算（design.md §9.1）。所属している部門と管理できる部門、全社・システムのタスクを管理できる人と所属のない人
 * にはシステムの枠の、今月の利用額・上限・残額と、実行回数・同時実行数（§9.2）を出す（見るだけ）
 */
const api = useAPI()
const { data: budgets, status } = await useAsyncData('my-budgets', () => api.account.budgets())
</script>

<template>
  <UDashboardPanel id="usage">
    <template #header>
      <UDashboardNavbar :title="$t('利用量')" />
    </template>
    <template #body>
      <UContainer>
        <p class="mb-4 text-sm text-muted">
          {{
            $t(
              'LLMの利用額を、部門ごととシステムの枠で月ごとに数えます。会話は会話で選んだ部門、タスクはタスクの部門（全社・システムのタスクはシステムの枠）に入ります。上限に達すると、新しい依頼を受け付けず、動いている作業もまとめて止めます。月の実行回数と同時実行数にも上限があり、達すると新しい依頼を受け付けません',
            )
          }}
        </p>
        <div v-if="status === 'pending'" class="text-sm text-muted">{{ $t('読み込み中') }}</div>
        <div v-else-if="!budgets?.length" class="text-sm text-muted">{{ $t('見られる予算はありません') }}</div>
        <div v-else class="grid gap-3 md:grid-cols-2">
          <UCard v-for="budget in budgets" :key="budget.department?.id ?? 'system'">
            <template #header>
              <div class="flex items-center gap-2">
                <span class="font-semibold">{{ budget.department?.name ?? $t('システムの枠') }}</span>
                <UBadge v-if="budget.joined" color="neutral" variant="subtle" size="sm">{{ $t('所属') }}</UBadge>
              </div>
            </template>
            <BudgetStatusView :status="budget.status" />
          </UCard>
        </div>
      </UContainer>
    </template>
  </UDashboardPanel>
</template>
