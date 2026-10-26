<script setup lang="ts">
const api = useAPI()
const toast = useToast()
const { t } = useI18n()
const modelValue = defineModel<ManageUser | null>()
// 操作する人が変えられないアカウント（canChange が false）では、入力を止める
const props = defineProps<{ disabled?: boolean }>()
const emit = defineEmits<{
  created: [value: ManageUserCreated]
  updated: [value: ManageUser]
  failed: [error: unknown]
}>()

const emptyForm = () => ({
  id: null as string | null,
  email: '',
  displayName: '',
  groups: [] as string[],
  isActive: true,
})
const form = ref(emptyForm())

watch(
  modelValue,
  (value) => {
    form.value = value
      ? {
          id: value.id,
          email: value.email,
          displayName: value.displayName ?? '',
          groups: [...(value.groups ?? [])],
          isActive: value.isActive ?? true,
        }
      : emptyForm()
  },
  { immediate: true },
)

const isEdit = computed(() => Boolean(form.value.id))

// Group（permissionの集まり）は少ないので、全件を選択肢にする（defineExpose より前で await しないよう、待たずに読み込む）
// 自分の持たないpermissionを含むGroupは、サーバーが付け外しを断る
const { data: groups } = useAsyncData('manage-groups', () => api.manage.groups.list())
const groupItems = computed(() => (groups.value ?? []).map((g) => ({ value: g.name, title: g.name })))

const { onSubmit, loading, errors } = createSubmit({
  form,
  fn: ({ id, email, ...rest }): Promise<ManageUser | ManageUserCreated> =>
    // メールアドレスは SSO の紐付けに使うので、作成した後は変えない
    id ? api.manage.users(id).patch(rest) : api.manage.users().create({ email, ...rest }),
  onSuccess: (result) => {
    if (form.value.id) emit('updated', result)
    else emit('created', result as ManageUserCreated)
    modelValue.value = result
    toast.add({ title: t('保存しました'), color: 'success' })
  },
  onError: (err) => {
    emit('failed', err)
    toast.add({ title: t('エラー'), description: makeErrorMsg(err), color: 'error' })
  },
})

// 必須項目が埋まっているかだけを確認する。形式の検証はサーバーに任せる
const canPost = computed(() => !props.disabled && Boolean(form.value.email))

defineExpose({ onSubmit, loading, canPost })
</script>

<template>
  <DFormStack>
    <DFormAlert :errors="errors" />
    <DTextField
      v-model="form.email"
      type="email"
      :label="$t('メールアドレス')"
      required
      :disabled="isEdit"
      :hint="
        isEdit
          ? $t('作成した後は変更できません')
          : $t('SSOで初めてログインしたときに、同じメールアドレスのアカウントに紐付きます')
      "
      :error-messages="errors.email"
    />
    <DTextField
      v-model="form.displayName"
      :label="$t('表示名')"
      :disabled="disabled"
      :error-messages="errors.displayName"
    />
    <DSelect
      v-model="form.groups"
      :label="$t('Group')"
      :hint="$t('Groupはpermissionの集まりです。自分の持たないpermissionを含むGroupは付け外しできません')"
      :items="groupItems"
      :disabled="disabled"
      multiple
      :placeholder="$t('なし（通常の社員）')"
      :error-messages="errors.groups"
    />
    <DSwitch
      v-if="isEdit"
      v-model="form.isActive"
      :label="$t('有効')"
      :disabled="disabled"
      :hint="$t('無効にすると、ログインできなくなり、ログイン中のセッションも使えなくなります')"
      :error-messages="errors.isActive"
    />
  </DFormStack>
</template>
