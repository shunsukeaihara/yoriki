<script setup lang="ts">
/** 部門の作成と変更。所属は部門の詳細（DepartmentMemberList）で管理する */
const api = useAPI()
const toast = useToast()
const { t } = useI18n()
const modelValue = defineModel<Department | null>()
const emit = defineEmits<{
  created: [value: Department]
  updated: [value: Department]
  failed: [error: unknown]
}>()

const emptyForm = () => ({ id: null as string | null, name: '' })
const form = ref(emptyForm())

watch(
  modelValue,
  (value) => {
    form.value = value ? { id: value.id, name: value.name } : emptyForm()
  },
  { immediate: true },
)

const { onSubmit, loading, errors } = createSubmit({
  form,
  fn: ({ id, ...data }) => (id ? api.manage.departments(id).patch(data) : api.manage.departments().create(data)),
  onSuccess: (result) => {
    if (form.value.id) emit('updated', result)
    else emit('created', result)
    modelValue.value = result
    toast.add({ title: t('保存しました'), color: 'success' })
  },
  onError: (err) => {
    emit('failed', err)
    toast.add({ title: t('エラー'), description: makeErrorMsg(err), color: 'error' })
  },
})

const canPost = computed(() => Boolean(form.value.name.trim()))

defineExpose({ onSubmit, loading, canPost })
</script>

<template>
  <DFormStack>
    <DFormAlert :errors="errors" />
    <DTextField v-model="form.name" :label="$t('名前')" required :error-messages="errors.name" />
  </DFormStack>
</template>
