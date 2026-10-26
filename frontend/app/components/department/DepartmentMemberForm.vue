<script setup lang="ts">
/**
 * 部門の所属の追加。所属は変更せず、外して追加し直す。部門の中で何ができるかは、その人のpermissionで決まる
 * （authorization.md §4）。自分と、自分の持たない権限を持つアカウントの所属は、サーバーが断る
 */
const props = defineProps<{ departmentId: string }>()
const api = useAPI()
const toast = useToast()
const { t } = useI18n()
const emit = defineEmits<{ created: [value: DepartmentMembership]; failed: [error: unknown] }>()

const form = ref({ user: null as string | null })

const { onSubmit, loading, errors } = createSubmit({
  form,
  fn: ({ user }) => api.manage.departments(props.departmentId).members().create({ user: user! }),
  onSuccess: (result) => {
    emit('created', result)
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
      v-model="form.user"
      :label="$t('社員')"
      required
      :search-fn="api.manage.users().list"
      :query-params="{ isActive: true }"
      item-title="email"
      item-description="displayName"
      :error-messages="errors.user"
    />
  </DFormStack>
</template>
