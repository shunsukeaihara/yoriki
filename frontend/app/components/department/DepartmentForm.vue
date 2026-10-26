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

const emptyForm = () => ({ id: null as string | null, name: '', coversAllDepartments: false })
const form = ref(emptyForm())

watch(
  modelValue,
  (value) => {
    form.value = value
      ? { id: value.id, name: value.name, coversAllDepartments: value.coversAllDepartments ?? false }
      : emptyForm()
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
    <DSwitch
      v-model="form.coversAllDepartments"
      :label="$t('すべての部門の範囲を含む')"
      :hint="
        $t(
          'この部門に所属する人を、すべての部門に所属しているものとして扱います。すべての部門のConnectorを見る人の管理に使います',
        )
      "
      :error-messages="errors.coversAllDepartments"
    />
  </DFormStack>
</template>
