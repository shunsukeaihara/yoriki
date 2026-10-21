<script setup lang="ts">
/**
 * 会話のモデルと effort を切り替える。実行中の Run にも、次のステップから反映される（design.md §4.8）。
 * 個人情報・機密情報を扱う会話では、ローカルLLMのモデルだけを選べる（サーバーでも確かめる）。
 */
const props = defineProps<{ conversation: Conversation; models: AppModelProfile[] }>()
const emit = defineEmits<{ updated: [value: Conversation] }>()
const api = useAPI()
const toast = useToast()
const { t } = useI18n()

const modelItems = computed(() => {
  const items = props.models
    .filter((m) => !props.conversation.handlesSensitiveData || m.isLocal)
    .map((m) => ({ value: m.id, title: m.name, description: m.isLocal ? t('ローカルLLM') : undefined }))
  // 今のモデルが選べなくなっていても（無効にされた等）、表示は残す
  const current = props.conversation.modelProfileInfo
  if (!items.some((i) => i.value === current.id))
    items.unshift({ value: current.id, title: current.name, description: undefined })
  return items
})

const profile = computed(() => props.conversation.modelProfileInfo)
// 選択肢の値に空文字は使えない（Nuxt UI の制約）ので、「既定」（API では空文字）を別の値で表す
const DEFAULT_EFFORT = '__default__'
const effortItems = computed(() => [
  {
    value: DEFAULT_EFFORT,
    title: profile.value.defaultEffort ? t('既定（{effort}）', { effort: profile.value.defaultEffort }) : t('既定'),
  },
  ...profile.value.effortValues.map((v) => ({ value: v, title: v })),
])

const saving = ref(false)
const save = async (data: Partial<ConversationWrite>) => {
  saving.value = true
  try {
    const result = await api.conversations(props.conversation.id).patch(data)
    emit('updated', result)
    toast.add({
      title: props.conversation.activeRun ? t('切り替えました。次のステップから使います') : t('切り替えました'),
      color: 'success',
    })
  } catch (err) {
    toast.add({ title: t('エラー'), description: makeErrorMsg(err), color: 'error' })
  } finally {
    saving.value = false
  }
}

const onEffort = (value: unknown) => {
  const effort = value === DEFAULT_EFFORT || !value ? '' : String(value)
  if (effort !== (props.conversation.effort ?? '')) save({ effort })
}
</script>

<template>
  <div class="flex items-center gap-2">
    <UBadge v-if="conversation.handlesSensitiveData" color="warning" variant="subtle" icon="i-lucide-shield">
      {{ $t('機密') }}
    </UBadge>
    <DSelect
      :model-value="conversation.modelProfile"
      :label="$t('モデル')"
      :show-label="false"
      :items="modelItems"
      item-description="description"
      :disabled="saving"
      class="w-44 sm:w-48"
      size="sm"
      @update:model-value="(v) => v && v !== conversation.modelProfile && save({ modelProfile: String(v) })"
    />
    <DSelect
      v-if="profile.effortValues.length"
      :model-value="conversation.effort || DEFAULT_EFFORT"
      :label="$t('effort')"
      :show-label="false"
      :items="effortItems"
      :disabled="saving"
      class="w-32 sm:w-36"
      size="sm"
      @update:model-value="onEffort"
    />
  </div>
</template>
