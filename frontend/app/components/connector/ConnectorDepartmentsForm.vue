<script setup lang="ts">
/**
 * Connectorを使える部門（所属の軸。design.md §4.9.1）。所属していれば、見る・使う（説明、試し実行、会話）ことができる。
 * 部門を指定しなければ全社向け。足し引きできるのは自分が所属している部門だけで、全社向けにする・やめるのは
 * すべての部門の範囲を持つ人だけ（サーバーが確かめる）。版には入れず、保存するとすぐに効く
 */
const api = useAPI()
const toast = useToast()
const { t } = useI18n()
const modelValue = defineModel<Connector | null>()
const emit = defineEmits<{
  updated: [value: Connector]
  failed: [error: unknown]
}>()

const toForm = (value: Connector | null | undefined) => ({
  version: value?.version ?? 0,
  departments: [...(value?.departments ?? [])],
})
const form = ref(toForm(null))
const { reset } = syncConnectorForm(modelValue, form, toForm)

const sameSet = (a: string[], b: string[]) => a.length === b.length && a.every((v) => b.includes(v))
const dirty = computed(() => !sameSet(form.value.departments, modelValue.value?.departments ?? []))

const { onSubmit, loading, errors } = createSubmit({
  form,
  fn: ({ version, departments }) => api.manage.connectors(modelValue.value!.id).patch({ version, departments }),
  onSuccess: (result) => {
    emit('updated', result)
    modelValue.value = result
    reset()
    toast.add({ title: t('保存しました'), color: 'success' })
  },
  onError: (err) => {
    emit('failed', err)
    toast.add({ title: t('エラー'), description: makeErrorMsg(err), color: 'error' })
  },
})

const canPost = computed(() => Boolean(modelValue.value) && dirty.value)

defineExpose({ onSubmit, loading, canPost, reset })
</script>

<template>
  <DFormStack>
    <DFormAlert :errors="errors" />
    <DAutocomplete
      v-model="form.departments"
      :label="$t('使える部門')"
      multiple
      :search-fn="api.manage.connectors().departments"
      :initial-selected="modelValue?.departmentInfos"
      :hint="
        $t(
          '所属している社員が、このConnectorを見て使えます。部門を指定しなければ全社向けです。足し引きできるのは自分が所属している部門だけです',
        )
      "
      :error-messages="errors.departments"
    />
    <UAlert
      v-if="!form.departments.length"
      color="info"
      variant="subtle"
      icon="i-lucide-globe"
      :title="$t('全社向けです。すべての社員が使えます')"
    />
  </DFormStack>
</template>
