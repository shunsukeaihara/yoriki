<script setup lang="ts">
const api = useAPI()
const toast = useToast()
const { t } = useI18n()
const modelValue = defineModel<LLMConnection | null>()
const emit = defineEmits<{
  created: [value: LLMConnection]
  updated: [value: LLMConnection]
  failed: [error: unknown]
}>()

const emptyForm = () => ({
  id: null as string | null,
  name: '',
  kind: ProviderKind.OPENAI as ProviderKind,
  baseUrl: '',
  // API key は書き込み専用。編集では、入力したときだけ送る
  apiKey: '',
  clearApiKey: false,
  timeoutSeconds: 120 as number | null,
  maxConcurrency: 4 as number | null,
  requestsPerMinute: null as number | null,
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
          name: value.name,
          kind: value.kind,
          baseUrl: value.baseUrl ?? '',
          timeoutSeconds: value.timeoutSeconds ?? null,
          maxConcurrency: value.maxConcurrency ?? null,
          requestsPerMinute: value.requestsPerMinute ?? null,
          isActive: value.isActive ?? true,
        }
      : emptyForm()
  },
  { immediate: true },
)

const isEdit = computed(() => Boolean(form.value.id))

const { onSubmit, loading, errors } = createSubmit({
  form,
  fn: ({ id, kind, baseUrl, apiKey, clearApiKey, timeoutSeconds, maxConcurrency, ...rest }) => {
    // 空欄の数値は送らない（作成では既定値、更新では変更なし）
    const common = { ...rest, timeoutSeconds: timeoutSeconds ?? undefined, maxConcurrency: maxConcurrency ?? undefined }
    if (!id) {
      return api.manage.llmConnections().create({ ...common, kind, baseUrl: usesBaseUrl(kind) ? baseUrl : '', apiKey })
    }
    // 空文字の API key は「API key なし」（ローカルLLMだけ）。削除を選んだときだけ送る
    const key = clearApiKey ? { apiKey: '' } : apiKey ? { apiKey } : {}
    return api.manage.llmConnections(id).patch({ ...common, ...key })
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
  if (!f.name) return false
  if (isEdit.value) return true
  return (!requiresApiKey(f.kind) || Boolean(f.apiKey)) && (!usesBaseUrl(f.kind) || Boolean(f.baseUrl))
})

const apiKeyHint = computed(() => {
  const current = modelValue.value
  if (current?.hasApiKey)
    return t('登録済み（末尾 {hint}）。差し替えるときだけ入力します', { hint: current.apiKeyHint })
  if (current) return t('未登録')
  if (!requiresApiKey(form.value.kind)) return t('API keyが要らないサーバーでは空のままにします')
  return undefined
})

defineExpose({ onSubmit, loading, canPost })
</script>

<template>
  <DFormStack>
    <DFormAlert :errors="errors" />
    <DTextField v-model="form.name" :label="$t('名前')" required :error-messages="errors.name" />

    <template v-if="!isEdit">
      <DRadioGroup
        v-model="form.kind"
        :label="$t('種別')"
        :items="providerKindItems(t)"
        variant="card"
        required
        :error-messages="errors.kind"
      />
      <!-- 送ってよいデータかはソフトウェアでは判定できないので、利用者に案内する（design.md §4冒頭） -->
      <UAlert
        color="info"
        variant="subtle"
        icon="i-lucide-shield-alert"
        :description="$t('個人情報や機密情報を扱う場合は、ローカルLLMの利用を推奨します')"
      />
      <DTextField
        v-if="usesBaseUrl(form.kind)"
        v-model="form.baseUrl"
        :label="$t('base URL')"
        required
        placeholder="http://vllm.internal:8000/v1"
        :hint="$t('作成した後は変更できません')"
        :error-messages="errors.baseUrl"
      />
    </template>
    <template v-else>
      <!-- 種別と base URL（接続先）は作成した後は変えられない。変えるときは新しい接続を作る -->
      <DTextField
        :model-value="modelValue?.kindDisplay"
        :label="$t('種別')"
        disabled
        :hint="$t('作成した後は変更できません')"
      />
      <DTextField
        v-if="usesBaseUrl(form.kind)"
        :model-value="form.baseUrl"
        :label="$t('base URL')"
        disabled
        :hint="$t('作成した後は変更できません')"
      />
    </template>

    <DTextField
      v-model="form.apiKey"
      type="password"
      autocomplete="new-password"
      :label="$t('API key')"
      :required="!isEdit && requiresApiKey(form.kind)"
      :disabled="form.clearApiKey"
      :hint="apiKeyHint"
      :error-messages="errors.apiKey"
    />
    <DCheckbox
      v-if="isEdit && !requiresApiKey(form.kind) && modelValue?.hasApiKey"
      v-model="form.clearApiKey"
      :label="$t('API keyを削除する')"
    />

    <DFormSection :title="$t('上限')">
      <DNumberField
        v-model="form.timeoutSeconds"
        :label="$t('タイムアウト（秒）')"
        :min="1"
        :max="3600"
        :error-messages="errors.timeoutSeconds"
      />
      <DNumberField
        v-model="form.maxConcurrency"
        :label="$t('同時実行数の上限')"
        :min="1"
        :max="1000"
        :error-messages="errors.maxConcurrency"
      />
      <DNumberField
        v-model="form.requestsPerMinute"
        :label="$t('1分あたりの要求数の上限')"
        :min="1"
        :hint="$t('空欄は上限なし')"
        :error-messages="errors.requestsPerMinute"
      />
    </DFormSection>

    <DSwitch v-model="form.isActive" :label="$t('有効')" :error-messages="errors.isActive" />
  </DFormStack>
</template>
