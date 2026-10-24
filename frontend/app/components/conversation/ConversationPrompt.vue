<script setup lang="ts">
/**
 * 会話の入力欄。実行中も使える（design.md §11.4）：今の作業への追加の指示（確認待ちなら回答）か、
 * 今の作業の後に始める次の依頼かを選んで送る。実行中なら停止のボタンも出す。
 * ファイルはクリップのボタンか、入力欄へのドロップで添付する（アップロードは呼び出し側）。
 * 使う Skill は、ボタンか、行の始めか空白の後で `/` を打つと開く一覧から選ぶ（design.md §11.2）。Skill を指定した
 * 依頼は、Skill の版を依頼を始めるときに決めるので、実行中なら今の作業の後に始める。
 */
const props = defineProps<{
  activeRun?: Run | null
  /** 回答待ちのエージェントからの質問がある */
  hasOpenQuestion?: boolean
  attachments?: PendingAttachment[]
  /** 指定できる Skill */
  skills?: Skill[]
  sending?: boolean
  stopping?: boolean
  disabled?: boolean
}>()
const text = defineModel<string>({ required: true })
const mode = defineModel<string>('mode', { default: MessageMode.AUTO })
const selectedSkills = defineModel<Skill[]>('selectedSkills', { default: () => [] })
const emit = defineEmits<{
  submit: []
  stop: []
  attach: [files: File[]]
  removeAttachment: [key: string]
  retryAttachments: []
}>()
const { t } = useI18n()

const prompt = useTemplateRef<{ $el: HTMLElement }>('prompt')
const picker = useFilePicker(
  () => prompt.value?.$el,
  (files) => {
    if (!props.disabled) emit('attach', files)
  },
)

const skillPickerOpen = ref(false)
const skillSearch = ref('')
// 行の始めか空白の後に `/` を打ったら、その `/` を消して一覧を開く
watch(text, (value, old) => {
  if (!props.skills?.length || value.length !== (old?.length ?? 0) + 1) return
  if (!/(^|\s)\/$/.test(value)) return
  text.value = value.slice(0, -1)
  skillSearch.value = ''
  skillPickerOpen.value = true
})
const selectSkill = (skill: Skill) => {
  selectedSkills.value = [...selectedSkills.value, skill]
}
const removeSkill = (id: string) => {
  selectedSkills.value = selectedSkills.value.filter((s) => s.id !== id)
}

const attachmentsReady = computed(() => (props.attachments ?? []).every((a) => a.artifact))
const hasContent = computed(
  () => Boolean(text.value.trim()) || Boolean(props.attachments?.length) || selectedSkills.value.length > 0,
)

const modeItems = computed(() => [
  { value: MessageMode.AUTO, title: props.hasOpenQuestion ? t('回答として送る') : t('今の作業に追加') },
  { value: MessageMode.NEXT, title: t('次の依頼にする') },
])

const placeholder = computed(() => {
  if (props.activeRun && selectedSkills.value.length)
    return t('今の作業が終わった後に、Skillを使って始める依頼を入力してください')
  if (!props.activeRun)
    return props.skills?.length ? t('依頼を入力してください（/ でSkillを指定）') : t('依頼を入力してください')
  if (mode.value === MessageMode.NEXT) return t('今の作業が終わった後に始める依頼を入力してください')
  if (props.hasOpenQuestion) return t('質問への回答を入力してください')
  return t('今の作業への追加の指示を入力してください')
})

const canSubmit = computed(() => hasContent.value && attachmentsReady.value && !props.sending && !props.disabled)
const onSubmit = () => {
  if (canSubmit.value) emit('submit')
}
</script>

<template>
  <UChatPrompt
    ref="prompt"
    v-model="text"
    :placeholder="placeholder"
    :disabled="disabled"
    variant="subtle"
    :class="{ 'ring-2 ring-primary': picker.isOverDropZone.value }"
    @submit="onSubmit"
  >
    <template v-if="attachments?.length || selectedSkills.length" #header>
      <div class="grid gap-2">
        <ul v-if="selectedSkills.length" class="flex flex-wrap gap-2">
          <li v-for="skill in selectedSkills" :key="skill.id">
            <UBadge color="primary" variant="subtle" icon="i-lucide-sparkles" class="gap-1">
              {{ skill.name }}
              <DBtn
                icon="i-lucide-x"
                size="xs"
                variant="link"
                color="neutral"
                class="-me-1 p-0.5"
                :aria-label="$t('Skillの指定を外す')"
                @click="removeSkill(skill.id)"
              />
            </UBadge>
          </li>
        </ul>
        <ConversationAttachmentChips
          v-if="attachments?.length"
          :items="attachments"
          @remove="emit('removeAttachment', $event)"
          @retry="emit('retryAttachments')"
        />
      </div>
    </template>
    <template #footer>
      <div class="flex items-center gap-2">
        <DBtn
          icon="i-lucide-paperclip"
          variant="ghost"
          color="neutral"
          size="sm"
          :disabled="disabled"
          :aria-label="$t('ファイルを添付')"
          @click="picker.open()"
        />
        <ConversationSkillPicker
          v-if="skills?.length"
          v-model:open="skillPickerOpen"
          v-model:search-term="skillSearch"
          :skills="skills"
          :selected="selectedSkills.map((s) => s.id)"
          :disabled="disabled"
          @select="selectSkill"
        />
        <DSelectButton
          v-if="activeRun && !selectedSkills.length"
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
        <UChatPromptSubmit :status="sending ? 'submitted' : 'ready'" :disabled="!canSubmit" />
      </div>
    </template>
  </UChatPrompt>
</template>
