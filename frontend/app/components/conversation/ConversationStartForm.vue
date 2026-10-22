<script setup lang="ts">
/**
 * 会話を始めるウィザード（design.md §11.3）。個人情報や機密情報を扱うかを先に答えてもらい、扱う場合は
 * ローカルLLMのモデルだけを選べるようにする（サーバーでも確かめる）。モデルは既定のものを選んでおく。
 */
const props = defineProps<{ models: AppModelProfile[] }>()
const emit = defineEmits<{ started: [conversation: Conversation, message: Message | null, error: unknown] }>()
const api = useAPI()
const { t } = useI18n()

const form = ref({
  handlesSensitiveData: null as boolean | null,
  modelProfile: null as string | null,
  body: '',
})

const sensitiveItems = computed(() => [
  { value: false, title: t('扱わない'), description: t('すべてのモデルから選べます') },
  { value: true, title: t('扱う'), description: t('社内で動かすローカルLLMのモデルだけを使います') },
])

const selectable = computed(() => props.models.filter((m) => !form.value.handlesSensitiveData || m.isLocal))
const modelItems = computed(() =>
  selectable.value.map((m) => ({ value: m.id, title: m.name, description: m.isLocal ? t('ローカルLLM') : undefined })),
)

// 扱う内容を選び直したら、選べるモデルの中の既定（なければ先頭）を選び直す
watch(
  () => form.value.handlesSensitiveData,
  () => {
    if (selectable.value.some((m) => m.id === form.value.modelProfile)) return
    form.value.modelProfile = (selectable.value.find((m) => m.isDefault) ?? selectable.value[0])?.id ?? null
  },
)

const { onSubmit, loading, errors } = createSubmit({
  form,
  fn: async ({ handlesSensitiveData, modelProfile, body }) => {
    const conversation = await api.conversations().create({
      handlesSensitiveData: Boolean(handlesSensitiveData),
      modelProfile: modelProfile ?? undefined,
    })
    // 会話は作れて最初の依頼だけ失敗した場合は、会話の画面へ移ってから知らせる
    try {
      const message = await api
        .conversations(conversation.id)
        .messages()
        .create({ body, clientMessageId: crypto.randomUUID(), mode: MessageMode.AUTO })
      return { conversation, message, error: null }
    } catch (error) {
      return { conversation, message: null, error }
    }
  },
  onSuccess: ({ conversation, message, error }) => emit('started', conversation, message, error),
})

const answered = computed(() => form.value.handlesSensitiveData !== null)
const canPost = computed(() => answered.value && Boolean(form.value.modelProfile && form.value.body.trim()))
const submit = () => {
  if (canPost.value && !loading.value) onSubmit()
}
</script>

<template>
  <div class="grid gap-6">
    <DFormAlert :errors="errors" />
    <DRadioGroup
      v-model="form.handlesSensitiveData"
      :label="$t('この会話で、個人情報や機密情報を扱いますか')"
      :items="sensitiveItems"
      variant="card"
      orientation="horizontal"
      required
      :error-messages="errors.handlesSensitiveData"
    />
    <UAlert
      v-if="form.handlesSensitiveData && !selectable.length"
      color="warning"
      variant="subtle"
      icon="i-lucide-triangle-alert"
      :title="$t('ローカルLLMのモデルが登録されていません。基盤管理者に問い合わせてください。')"
    />
    <DSelect
      v-model="form.modelProfile"
      :label="$t('モデル')"
      :items="modelItems"
      item-description="description"
      :disabled="!answered"
      required
      :error-messages="errors.modelProfile"
    />
    <UChatPrompt
      v-model="form.body"
      :placeholder="answered ? $t('依頼を入力してください') : $t('先に、扱う内容を選んでください')"
      :disabled="!answered"
      variant="subtle"
      autofocus
      @submit="submit"
    >
      <template #footer>
        <div />
        <UChatPromptSubmit :status="loading ? 'submitted' : 'ready'" :disabled="!canPost" />
      </template>
    </UChatPrompt>
  </div>
</template>
