<script setup lang="ts">
const api = useAPI()
const toast = useToast()
const { t } = useI18n()
const modelValue = defineModel<ModelProfile | null>()
const emit = defineEmits<{
  created: [value: ModelProfile]
  updated: [value: ModelProfile]
  failed: [error: unknown]
}>()

const emptyForm = () => ({
  id: null as string | null,
  // 編集を始めた時点の版。その間に他の人が版を作っていたら、サーバーが409で断る
  version: 0,
  name: '',
  isActive: true,
  connection: null as string | null,
  modelName: '',
  maxInputTokens: null as number | null,
  maxOutputTokens: null as number | null,
  inputPrice: '0' as string | null,
  cachedInputPrice: '0' as string | null,
  outputPrice: '0' as string | null,
  supportsTools: false,
  supportsImages: false,
  supportsDocuments: false,
  supportsAudio: false,
  supportsVideo: false,
  supportsStructuredOutput: false,
  supportsPromptCaching: false,
  supportsReasoning: false,
  // モデルが受け付けるeffortの値（各社の指定そのまま）。画面ではカンマ区切りで入力する
  effortValuesText: '',
  defaultEffort: '' as string | null,
  changeReason: '',
})
const form = ref(emptyForm())

watch(
  modelValue,
  (value) => {
    form.value = value
      ? {
          id: value.id,
          version: value.version ?? 0,
          name: value.name,
          isActive: value.isActive ?? true,
          connection: value.connection,
          modelName: value.modelName,
          maxInputTokens: value.maxInputTokens,
          maxOutputTokens: value.maxOutputTokens,
          inputPrice: value.inputPrice,
          cachedInputPrice: value.cachedInputPrice,
          outputPrice: value.outputPrice,
          supportsTools: value.supportsTools,
          supportsImages: value.supportsImages,
          supportsDocuments: value.supportsDocuments,
          supportsAudio: value.supportsAudio,
          supportsVideo: value.supportsVideo,
          supportsStructuredOutput: value.supportsStructuredOutput,
          supportsPromptCaching: value.supportsPromptCaching,
          supportsReasoning: value.supportsReasoning,
          effortValuesText: (value.effortValues ?? []).join(', '),
          defaultEffort: value.defaultEffort ?? '',
          changeReason: '',
        }
      : emptyForm()
  },
  { immediate: true },
)

const isEdit = computed(() => Boolean(form.value.id))

const { onSubmit, loading, errors } = createSubmit({
  form,
  fn: ({
    id,
    version,
    connection,
    maxInputTokens,
    maxOutputTokens,
    inputPrice,
    cachedInputPrice,
    outputPrice,
    effortValuesText,
    defaultEffort,
    ...rest
  }) => {
    // 必須項目は canPost で確認済み。空欄はサーバーの検証でエラーになる
    const data = {
      ...rest,
      connection: connection ?? '',
      maxInputTokens: maxInputTokens ?? 0,
      maxOutputTokens: maxOutputTokens ?? 0,
      inputPrice: inputPrice ?? '0',
      cachedInputPrice: cachedInputPrice ?? '0',
      outputPrice: outputPrice ?? '0',
      // 推論の制御に対応しないモデルには、effortの値を持たせない
      effortValues: rest.supportsReasoning ? splitEfforts(effortValuesText) : [],
      defaultEffort: rest.supportsReasoning ? (defaultEffort ?? '') : '',
    }
    return id ? api.manage.modelProfiles(id).patch({ ...data, version }) : api.manage.modelProfiles().create(data)
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

const splitEfforts = (text: string) =>
  text
    .split(',')
    .map((v) => v.trim())
    .filter(Boolean)
const effortItems = computed(() => splitEfforts(form.value.effortValuesText).map((v) => ({ value: v, title: v })))

// 必須項目が埋まっているかだけを確認する。形式の検証はサーバーに任せる
const canPost = computed(() => {
  const f = form.value
  return Boolean(f.name && f.connection && f.modelName && f.maxInputTokens && f.maxOutputTokens)
})

// 接続先で使えるモデル名を取得し、選べるようにする
const models = ref<string[]>([])
const modelsLoading = ref(false)
watch(
  () => form.value.connection,
  () => (models.value = []),
)
const loadModels = async () => {
  if (!form.value.connection) return
  modelsLoading.value = true
  try {
    const result = await api.manage.llmConnections(form.value.connection).check()
    if (result.ok) models.value = result.models
    else toast.add({ title: t('接続できませんでした'), description: result.detail, color: 'error' })
  } catch (err) {
    toast.add({ title: t('エラー'), description: makeErrorMsg(err), color: 'error' })
  } finally {
    modelsLoading.value = false
  }
}

defineExpose({ onSubmit, loading, canPost })
</script>

<template>
  <DFormStack>
    <DFormAlert :errors="errors" />
    <DTextField v-model="form.name" :label="$t('名前')" required :error-messages="errors.name" />
    <DSwitch v-model="form.isActive" :label="$t('有効')" :error-messages="errors.isActive" />

    <DFormSection
      :title="$t('モデル')"
      :description="
        isEdit
          ? $t('この欄から下を変えて保存すると、新しい版になります。進行中の実行は元の版を使い続けます')
          : undefined
      "
    >
      <!-- 有効な接続だけを選べる（サーバーでも確認する） -->
      <DAutocomplete
        v-model="form.connection"
        :label="$t('LLMの接続')"
        required
        :search-fn="api.manage.llmConnections().list"
        :query-params="{ isActive: true }"
        item-description="kindDisplay"
        :initial-selected="modelValue?.connectionInfo"
        :clearable="false"
        :error-messages="errors.connection"
      />
      <DTextField v-model="form.modelName" :label="$t('モデル名')" required :error-messages="errors.modelName">
        <template #append>
          <DBtn
            size="xs"
            color="neutral"
            variant="link"
            :disabled="!form.connection"
            :loading="modelsLoading"
            @click="loadModels"
          >
            {{ $t('接続先から選ぶ') }}
          </DBtn>
        </template>
      </DTextField>
      <div v-if="models.length" class="flex max-h-40 flex-wrap gap-1 overflow-y-auto">
        <DBtn
          v-for="name in models"
          :key="name"
          size="xs"
          :color="name === form.modelName ? 'primary' : 'neutral'"
          variant="soft"
          @click="form.modelName = name"
        >
          {{ name }}
        </DBtn>
      </div>
      <div class="grid grid-cols-2 gap-4">
        <DNumberField
          v-model="form.maxInputTokens"
          :label="$t('最大入力token')"
          required
          :min="1"
          :error-messages="errors.maxInputTokens"
        />
        <DNumberField
          v-model="form.maxOutputTokens"
          :label="$t('最大出力token')"
          required
          :min="1"
          :error-messages="errors.maxOutputTokens"
        />
      </div>
    </DFormSection>

    <DFormSection
      :title="$t('単価')"
      :description="$t('100万tokenあたりの米ドル。ローカルLLMは0か、設定した原価にします')"
    >
      <div class="grid grid-cols-3 gap-4">
        <DDecimalField
          v-model="form.inputPrice"
          :label="$t('入力')"
          :decimal-places="6"
          :min="0"
          :error-messages="errors.inputPrice"
        />
        <DDecimalField
          v-model="form.cachedInputPrice"
          :label="$t('キャッシュされた入力')"
          :decimal-places="6"
          :min="0"
          :error-messages="errors.cachedInputPrice"
        />
        <DDecimalField
          v-model="form.outputPrice"
          :label="$t('出力')"
          :decimal-places="6"
          :min="0"
          :error-messages="errors.outputPrice"
        />
      </div>
    </DFormSection>

    <DFormSection :title="$t('対応する能力')">
      <DCheckbox v-model="form.supportsTools" :label="$t('Tool呼出し')" :error-messages="errors.supportsTools" />
      <DCheckbox v-model="form.supportsImages" :label="$t('画像入力')" :error-messages="errors.supportsImages" />
      <DCheckbox
        v-model="form.supportsDocuments"
        :label="$t('文書入力（PDFをそのまま渡せる）')"
        :error-messages="errors.supportsDocuments"
      />
      <DCheckbox v-model="form.supportsAudio" :label="$t('音声入力')" :error-messages="errors.supportsAudio" />
      <DCheckbox v-model="form.supportsVideo" :label="$t('動画入力')" :error-messages="errors.supportsVideo" />
      <DCheckbox
        v-model="form.supportsStructuredOutput"
        :label="$t('構造化出力')"
        :error-messages="errors.supportsStructuredOutput"
      />
      <DCheckbox
        v-model="form.supportsPromptCaching"
        :label="$t('prompt caching')"
        :error-messages="errors.supportsPromptCaching"
      />
      <DCheckbox
        v-model="form.supportsReasoning"
        :label="$t('推論の制御')"
        :error-messages="errors.supportsReasoning"
      />
      <template v-if="form.supportsReasoning">
        <DTextField
          v-model="form.effortValuesText"
          :label="$t('effortの値')"
          :hint="$t('モデルが受け付ける値をカンマ区切りで入力します（例：low, medium, high）。各社の指定そのままです')"
          :error-messages="errors.effortValues"
        />
        <DSelect
          v-model="form.defaultEffort"
          :label="$t('既定のeffort')"
          :items="effortItems"
          clearable
          :error-messages="errors.defaultEffort"
        />
      </template>
    </DFormSection>

    <DTextarea
      v-model="form.changeReason"
      :label="$t('変更理由')"
      :hint="$t('版の履歴に残ります')"
      :error-messages="errors.changeReason"
    />
  </DFormStack>
</template>
