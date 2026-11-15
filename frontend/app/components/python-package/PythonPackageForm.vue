<script setup lang="ts">
/** 追加してよいPythonのpackageの登録と変更（design.md §7.6） */
const api = useAPI()
const toast = useToast()
const { t } = useI18n()
const { hasPermission } = usePermission()
const modelValue = defineModel<PythonPackage | null>()
const emit = defineEmits<{
  created: [value: PythonPackage]
  updated: [value: PythonPackage]
  failed: [error: unknown]
}>()

const emptyForm = () => ({ id: null as string | null, name: '', specifier: '', note: '', isActive: true })
const form = ref(emptyForm())

watch(
  modelValue,
  (value) => {
    form.value = value
      ? {
          id: value.id,
          name: value.name,
          specifier: value.specifier ?? '',
          note: value.note ?? '',
          isActive: value.isActive ?? true,
        }
      : emptyForm()
  },
  { immediate: true },
)

const readonly = computed(() =>
  form.value.id
    ? !hasPermission(PermissionCode.CHANGE_PYTHON_PACKAGE)
    : !hasPermission(PermissionCode.ADD_PYTHON_PACKAGE),
)

const { onSubmit, loading, errors } = createSubmit({
  form,
  fn: ({ id, ...data }) => (id ? api.manage.pythonPackages(id).patch(data) : api.manage.pythonPackages().create(data)),
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

const canPost = computed(() => !readonly.value && Boolean(form.value.name.trim()))

defineExpose({ onSubmit, loading, canPost })
</script>

<template>
  <DFormStack>
    <DFormAlert :errors="errors" />
    <DTextField
      v-model="form.name"
      :label="$t('名前')"
      :hint="
        $t('PyPI等でのpackageの名前（例：tabulate）。大文字・小文字と「-」「_」「.」の違いは同じものとして扱います')
      "
      required
      :disabled="readonly"
      :error-messages="errors.name"
    />
    <DTextField
      v-model="form.specifier"
      :label="$t('版の範囲')"
      :hint="$t('許す版をPEP 440の書き方で指定します（例：>=0.9,<1）。空ならどの版も許します')"
      :disabled="readonly"
      :error-messages="errors.specifier"
    />
    <DTextField v-model="form.note" :label="$t('メモ')" :disabled="readonly" :error-messages="errors.note" />
    <DSwitch
      v-model="form.isActive"
      :label="$t('有効')"
      :hint="$t('無効にすると新しく追加できなくなり、追加済みの会話でも次に作業環境を作るときに入れません')"
      :disabled="readonly"
    />
  </DFormStack>
</template>
