<script setup lang="ts">
/**
 * 会話の入力欄。実行中も使える（design.md §11.4）：今の作業への追加の指示（確認待ちなら回答）か、
 * 今の作業の後に始める次の依頼かを選んで送る。実行中なら停止のボタンも出す。
 */
const props = defineProps<{
  activeRun?: Run | null
  /** 回答待ちのエージェントからの質問がある */
  hasOpenQuestion?: boolean
  sending?: boolean
  stopping?: boolean
  disabled?: boolean
}>()
const text = defineModel<string>({ required: true })
const mode = defineModel<string>('mode', { default: MessageMode.AUTO })
const emit = defineEmits<{ submit: []; stop: [] }>()
const { t } = useI18n()

const modeItems = computed(() => [
  { value: MessageMode.AUTO, title: props.hasOpenQuestion ? t('回答として送る') : t('今の作業に追加') },
  { value: MessageMode.NEXT, title: t('次の依頼にする') },
])

const placeholder = computed(() => {
  if (!props.activeRun) return t('依頼を入力してください')
  if (mode.value === MessageMode.NEXT) return t('今の作業が終わった後に始める依頼を入力してください')
  if (props.hasOpenQuestion) return t('質問への回答を入力してください')
  return t('今の作業への追加の指示を入力してください')
})

const onSubmit = () => {
  if (text.value.trim() && !props.sending) emit('submit')
}
</script>

<template>
  <UChatPrompt v-model="text" :placeholder="placeholder" :disabled="disabled" variant="subtle" @submit="onSubmit">
    <template #footer>
      <div class="flex items-center gap-2">
        <DSelectButton
          v-if="activeRun"
          v-model="mode"
          :label="$t('送り方')"
          :show-label="false"
          :items="modeItems"
          size="xs"
        />
      </div>
      <div class="flex items-center gap-2">
        <DBtn
          v-if="activeRun && isCancellable(activeRun)"
          color="neutral"
          variant="outline"
          icon="i-lucide-square"
          :loading="stopping"
          @click="emit('stop')"
        >
          {{ $t('停止') }}
        </DBtn>
        <UChatPromptSubmit :status="sending ? 'submitted' : 'ready'" :disabled="!text.trim() || disabled" />
      </div>
    </template>
  </UChatPrompt>
</template>
