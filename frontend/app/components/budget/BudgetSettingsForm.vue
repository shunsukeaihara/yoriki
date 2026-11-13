<script setup lang="ts">
/** 予算の既定の上限（金額は design.md §9.1、実行回数・同時実行数は §9.2、1回の実行の金額は §9.4）。空なら無制限。部門の上限は部門ごとに上書きできる */
const props = defineProps<{ settings: BudgetSettings; readonly?: boolean }>()
const emit = defineEmits<{ updated: [value: BudgetSettings] }>()
const api = useAPI()
const toast = useToast()
const { t } = useI18n()

const toForm = (value: BudgetSettings) => ({
  departmentMonthlyLimit: value.departmentMonthlyLimit,
  departmentMonthlyRuns: value.departmentMonthlyRuns,
  departmentConcurrentRuns: value.departmentConcurrentRuns,
  systemMonthlyLimit: value.systemMonthlyLimit,
  systemMonthlyRuns: value.systemMonthlyRuns,
  systemConcurrentRuns: value.systemConcurrentRuns,
  runCostLimit: value.runCostLimit,
})
const form = ref(toForm(props.settings))
const saved = ref(JSON.stringify(form.value))
watch(
  () => props.settings,
  (value) => {
    form.value = toForm(value)
    saved.value = JSON.stringify(form.value)
  },
)
const dirty = computed(() => JSON.stringify(form.value) !== saved.value)

const { onSubmit, loading, errors } = createSubmit({
  form,
  fn: (data) => api.manage.budgetSettings.patch(data),
  onSuccess: (result) => {
    emit('updated', result)
    toast.add({ title: t('保存しました'), color: 'success' })
  },
  onError: (err) => toast.add({ title: t('エラー'), description: makeErrorMsg(err), color: 'error' }),
})
</script>

<template>
  <DFormStack>
    <DFormAlert :errors="errors" />
    <DDecimalField
      v-model="form.departmentMonthlyLimit"
      :label="$t('部門の月の上限（米ドル）')"
      :hint="$t('空なら無制限です。部門ごとに上書きできます')"
      :decimal-places="2"
      :min="0"
      :disabled="readonly"
      :error-messages="errors.departmentMonthlyLimit"
    />
    <DNumberField
      v-model="form.departmentMonthlyRuns"
      :label="$t('部門の月の実行回数の上限')"
      :hint="$t('会話の発言・タスク・評価で始めた実行を数えます。空なら無制限です')"
      :min="0"
      :disabled="readonly"
      :error-messages="errors.departmentMonthlyRuns"
    />
    <DNumberField
      v-model="form.departmentConcurrentRuns"
      :label="$t('部門の同時実行数の上限')"
      :hint="$t('確認待ちの実行は数えません。空なら無制限です')"
      :min="0"
      :disabled="readonly"
      :error-messages="errors.departmentConcurrentRuns"
    />
    <DDecimalField
      v-model="form.systemMonthlyLimit"
      :label="$t('システムの枠の月の上限（米ドル）')"
      :hint="$t('全社・システムのタスクと、所属のない人の利用が入ります。空なら無制限です')"
      :decimal-places="2"
      :min="0"
      :disabled="readonly"
      :error-messages="errors.systemMonthlyLimit"
    />
    <DNumberField
      v-model="form.systemMonthlyRuns"
      :label="$t('システムの枠の月の実行回数の上限')"
      :hint="$t('空なら無制限です')"
      :min="0"
      :disabled="readonly"
      :error-messages="errors.systemMonthlyRuns"
    />
    <DNumberField
      v-model="form.systemConcurrentRuns"
      :label="$t('システムの枠の同時実行数の上限')"
      :hint="$t('空なら無制限です')"
      :min="0"
      :disabled="readonly"
      :error-messages="errors.systemConcurrentRuns"
    />
    <DDecimalField
      v-model="form.runCostLimit"
      :label="$t('1回の実行の金額の上限（米ドル）')"
      :hint="$t('会話の発言1回・タスクの実行1回ごとの、LLMの利用額の上限です。空なら無制限です')"
      :decimal-places="2"
      :min="0.01"
      :disabled="readonly"
      :error-messages="errors.runCostLimit"
    />
    <div v-if="!readonly">
      <DBtn :disabled="!dirty" :loading="loading" @click="onSubmit">{{ $t('保存') }}</DBtn>
    </div>
  </DFormStack>
</template>
