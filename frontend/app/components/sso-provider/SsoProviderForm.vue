<script setup lang="ts">
const api = useAPI()
const toast = useToast()
const { t } = useI18n()
const { copy } = useClipboard()
const modelValue = defineModel<SSOProvider | null>()
const emit = defineEmits<{
  created: [value: SSOProvider]
  updated: [value: SSOProvider]
  failed: [error: unknown]
}>()

const emptyForm = () => ({
  id: null as string | null,
  kind: SSOKind.MICROSOFT as SSOKind,
  clientId: '',
  // client secret は書き込み専用。編集では、入力したときだけ送る
  clientSecret: '',
  tenantId: '',
  hostedDomain: '',
  // 1行に1つ。送るときに配列にする
  allowedEmailDomains: '',
  isActive: true,
})
const form = ref(emptyForm())

watch(
  modelValue,
  (value) => {
    form.value = value
      ? {
          ...emptyForm(),
          id: value.id,
          kind: value.kind,
          clientId: value.clientId,
          tenantId: value.tenantId ?? '',
          hostedDomain: value.hostedDomain ?? '',
          allowedEmailDomains: value.allowedEmailDomains.join('\n'),
          isActive: value.isActive ?? true,
        }
      : emptyForm()
  },
  { immediate: true },
)

const isEdit = computed(() => Boolean(form.value.id))
const isMicrosoft = computed(() => form.value.kind === SSOKind.MICROSOFT)

const { onSubmit, loading, errors } = createSubmit({
  form,
  fn: ({ id, kind, clientSecret, tenantId, hostedDomain, allowedEmailDomains, ...rest }) => {
    // 種別に関係しない項目は空にして送る（サーバーは、その種別で使わない項目に値があると入力エラーにする）
    const data = {
      ...rest,
      tenantId: kind === SSOKind.MICROSOFT ? tenantId : '',
      hostedDomain: kind === SSOKind.GOOGLE ? hostedDomain : '',
      allowedEmailDomains: parseDomains(allowedEmailDomains),
    }
    if (!id) return api.manage.ssoProviders().create({ ...data, kind, clientSecret })
    return api.manage.ssoProviders(id).patch({ ...data, ...(clientSecret ? { clientSecret } : {}) })
  },
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

// 必須項目が埋まっているかだけを確認する。形式の検証はサーバーに任せる
const canPost = computed(() => {
  const f = form.value
  if (!f.clientId || parseDomains(f.allowedEmailDomains).length === 0) return false
  if (!isEdit.value && !f.clientSecret) return false
  return isMicrosoft.value ? Boolean(f.tenantId) : Boolean(f.hostedDomain)
})

const callbackUrl = computed(() => modelValue.value?.callbackUrl ?? ssoCallbackUrl(form.value.kind))
const copyCallbackUrl = async () => {
  await copy(callbackUrl.value)
  toast.add({ title: t('コピーしました'), color: 'success' })
}

const clientSecretHint = computed(() => {
  const current = modelValue.value
  if (current?.hasClientSecret)
    return t('登録済み（末尾 {hint}）。差し替えるときだけ入力します', { hint: current.clientSecretHint })
  return undefined
})

defineExpose({ onSubmit, loading, canPost })
</script>

<template>
  <DFormStack>
    <DFormAlert :errors="errors" />

    <DRadioGroup
      v-if="!isEdit"
      v-model="form.kind"
      :label="$t('種別')"
      :items="ssoKindItems(t)"
      variant="card"
      required
      :error-messages="errors.kind"
    />
    <!-- 種別は作成した後は変えられない。変えるときは削除して作り直す -->
    <DTextField
      v-else
      :model-value="modelValue?.kindDisplay"
      :label="$t('種別')"
      disabled
      :hint="$t('作成した後は変更できません')"
    />

    <DFormField
      :label="$t('リダイレクトURI')"
      :hint="$t('認証基盤のアプリの登録に、このURLをリダイレクトURIとして登録します')"
    >
      <div class="flex items-center gap-2">
        <code class="min-w-0 flex-1 truncate rounded bg-elevated px-2 py-1.5 text-xs">{{ callbackUrl }}</code>
        <DBtn size="xs" color="neutral" variant="outline" icon="i-lucide-copy" @click="copyCallbackUrl">
          {{ $t('コピー') }}
        </DBtn>
      </div>
    </DFormField>

    <DTextField v-model="form.clientId" :label="$t('client ID')" required :error-messages="errors.clientId" />
    <DTextField
      v-model="form.clientSecret"
      type="password"
      autocomplete="new-password"
      :label="$t('client secret')"
      :required="!isEdit"
      :hint="clientSecretHint"
      :error-messages="errors.clientSecret"
    />
    <DTextField
      v-if="isMicrosoft"
      v-model="form.tenantId"
      :label="$t('テナントID')"
      required
      :hint="$t('Entra IDの「ディレクトリ（テナント）ID」。他のテナントのアカウントは受け付けません')"
      :error-messages="errors.tenantId"
    />
    <DTextField
      v-else
      v-model="form.hostedDomain"
      :label="$t('Workspaceのドメイン')"
      required
      placeholder="example.co.jp"
      :hint="$t('このドメインのWorkspaceのアカウントだけを受け付けます')"
      :error-messages="errors.hostedDomain"
    />
    <DTextarea
      v-model="form.allowedEmailDomains"
      :label="$t('許可するメールアドレスのドメイン')"
      required
      placeholder="example.co.jp"
      :hint="$t('1行に1つ。メールアドレスがこのドメインのアカウントだけがログインできます')"
      :error-messages="flattenRowErrors(errors.allowedEmailDomains)"
    />
    <DSwitch v-model="form.isActive" :label="$t('有効')" :error-messages="errors.isActive" />
    <UAlert
      color="warning"
      variant="subtle"
      icon="i-lucide-triangle-alert"
      :description="
        $t(
          '有効なSSOの設定があると、社員はSSOだけでログインします。設定を誤った場合に備えて、基盤管理者はパスワードでもログインできます',
        )
      "
    />
  </DFormStack>
</template>
