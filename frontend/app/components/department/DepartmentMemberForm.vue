<script setup lang="ts">
/** 部門の所属の追加と役割の変更。社員は追加するときだけ選べる（別の社員にするなら、外して追加し直す） */
const props = defineProps<{ departmentId: string }>()
const api = useAPI()
const toast = useToast()
const { t } = useI18n()
const modelValue = defineModel<DepartmentMembership | null>()
const emit = defineEmits<{
  created: [value: DepartmentMembership]
  updated: [value: DepartmentMembership]
  failed: [error: unknown]
}>()

const emptyForm = () => ({
  id: null as string | null,
  user: null as string | null,
  role: DepartmentRole.MEMBER as DepartmentRoleEnum,
})
const form = ref(emptyForm())

watch(
  modelValue,
  (value) => {
    form.value = value ? { id: value.id, user: value.user, role: value.role } : emptyForm()
  },
  { immediate: true },
)

const { onSubmit, loading, errors } = createSubmit({
  form,
  fn: ({ id, user, role }) =>
    id
      ? api.manage.departments(props.departmentId).members(id).patch({ role })
      : api.manage.departments(props.departmentId).members().create({ user: user!, role }),
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

const canPost = computed(() => Boolean(form.value.user))

defineExpose({ onSubmit, loading, canPost })
</script>

<template>
  <DFormStack>
    <DFormAlert :errors="errors" />
    <DAutocomplete
      v-if="!form.id"
      v-model="form.user"
      :label="$t('社員')"
      required
      :search-fn="api.manage.users().list"
      :query-params="{ isActive: true }"
      item-title="email"
      item-description="displayName"
      :error-messages="errors.user"
    />
    <p v-else class="text-sm">{{ modelValue?.userInfo.displayName || modelValue?.userInfo.email }}</p>
    <DRadioGroup
      v-model="form.role"
      :label="$t('部門の中の役割')"
      :items="departmentRoleItems(t)"
      variant="card"
      :error-messages="errors.role"
    />
  </DFormStack>
</template>
