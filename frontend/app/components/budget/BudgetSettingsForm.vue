<script setup lang="ts">
/** 予算の既定の上限（design.md §9.1）。空なら無制限。部門の上限は部門ごとに上書きできる */
const props = defineProps<{ settings: BudgetSettings; readonly?: boolean }>()
const emit = defineEmits<{ updated: [value: BudgetSettings] }>()
const api = useAPI()
const toast = useToast()
const { t } = useI18n()

const toForm = (value: BudgetSettings) => ({
  departmentMonthlyLimit: value.departmentMonthlyLimit,
  companyMonthlyLimit: value.companyMonthlyLimit,
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
    <DDecimalField
      v-model="form.companyMonthlyLimit"
      :label="$t('全社の枠の月の上限（米ドル）')"
      :hint="$t('全社・システムのタスクと、所属のない人の利用が入ります。空なら無制限です')"
      :decimal-places="2"
      :min="0"
      :disabled="readonly"
      :error-messages="errors.companyMonthlyLimit"
    />
    <div v-if="!readonly">
      <DBtn :disabled="!dirty" :loading="loading" @click="onSubmit">{{ $t('保存') }}</DBtn>
    </div>
  </DFormStack>
</template>
