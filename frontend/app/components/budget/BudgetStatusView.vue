<script setup lang="ts">
/** 今月の利用額・上限・残額。上限があれば使った割合を棒で出す（予約中の分も使ったものとして数える） */
const props = defineProps<{ status: BudgetStatus }>()
const { t } = useI18n()
const percent = computed(() => budgetUsedPercent(props.status))
const color = computed(() => {
  if (percent.value === null) return 'primary'
  if (percent.value >= 100) return 'error'
  return percent.value >= 80 ? 'warning' : 'primary'
})
</script>

<template>
  <div class="flex flex-col gap-1">
    <div class="flex flex-wrap gap-x-4 text-sm">
      <span>{{ $t('利用額') }}：{{ formatUsd(status.spent, '') }}</span>
      <span>{{ $t('上限') }}：{{ formatUsd(status.limit, t('無制限')) }}</span>
      <span>{{ $t('残額') }}：{{ formatUsd(status.remaining, t('無制限')) }}</span>
    </div>
    <UProgress v-if="percent !== null" :model-value="percent" :color="color" size="sm" />
  </div>
</template>
