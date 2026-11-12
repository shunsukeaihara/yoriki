<script setup lang="ts">
/** 部門の上限（金額・月の実行回数・同時実行数）の上書き。空なら既定に従い、0ならこの部門では使わせない（design.md §9.1・§9.2） */
import DDialog from '../common/DDialog.vue'

const props = defineProps<{ budget: DepartmentBudget }>()
const emit = defineEmits<{ saved: [value: DepartmentBudget] }>()
const api = useAPI()
const toast = useToast()
const { t } = useI18n()
const dialog = ref<InstanceType<typeof DDialog> | null>(null)

const form = ref({
  monthlyLimit: null as string | null,
  monthlyRuns: null as number | null,
  concurrentRuns: null as number | null,
})
const onOpen = (open: boolean) => {
  if (!open) return
  errors.value = {}
  form.value = {
    monthlyLimit: props.budget.monthlyLimit,
    monthlyRuns: props.budget.monthlyRuns,
    concurrentRuns: props.budget.concurrentRuns,
  }
}

const { onSubmit, loading, errors } = createSubmit({
  form,
  fn: (data) => api.manage.departmentBudgets(props.budget.id).patch(data),
  onSuccess: (result) => {
    emit('saved', result)
    dialog.value?.closeDialog()
    toast.add({ title: t('保存しました'), color: 'success' })
  },
  onError: (err) => toast.add({ title: t('エラー'), description: makeErrorMsg(err), color: 'error' }),
})
</script>

<template>
  <DDialog ref="dialog" @update:open="onOpen">
    <template #trigger="{ on }">
      <slot name="trigger" :on="on" />
    </template>
    <template #header>{{ $t('「{name}」の上限', { name: budget.name }) }}</template>
    <template #content>
      <DFormStack>
        <DFormAlert :errors="errors" />
        <DDecimalField
          v-model="form.monthlyLimit"
          :label="$t('月の上限（米ドル）')"
          :hint="$t('空なら既定に従います。0ならこの部門では使わせません')"
          :decimal-places="2"
          :min="0"
          :error-messages="errors.monthlyLimit"
        />
        <DNumberField
          v-model="form.monthlyRuns"
          :label="$t('月の実行回数の上限')"
          :hint="$t('空なら既定に従います。0ならこの部門では使わせません')"
          :min="0"
          :error-messages="errors.monthlyRuns"
        />
        <DNumberField
          v-model="form.concurrentRuns"
          :label="$t('同時実行数の上限')"
          :hint="$t('空なら既定に従います。0ならこの部門では使わせません')"
          :min="0"
          :error-messages="errors.concurrentRuns"
        />
      </DFormStack>
    </template>
    <template #footer="{ closeDialog }">
      <DBtn color="neutral" variant="ghost" @click="closeDialog()">{{ $t('キャンセル') }}</DBtn>
      <DBtn :loading="loading" @click="onSubmit">{{ $t('保存') }}</DBtn>
    </template>
  </DDialog>
</template>
