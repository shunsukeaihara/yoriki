<script setup lang="ts">
/**
 * 会話の入力欄。実行中も使える（design.md §11.4）：今の作業への追加の指示（確認待ちなら回答）か、
 * 今の作業の後に始める次の依頼かを選んで送る。実行中なら停止のボタンも出す。
 * ファイルはクリップのボタンか、入力欄へのドロップで添付する（アップロードは呼び出し側）。
 * 使う Skill は、ボタンか、行の始めか空白の後で `/` を打つと開く一覧から選ぶ（design.md §11.2）。Skill を指定した
 * 依頼は、Skill の版を依頼を始めるときに決めるので、実行中なら今の作業の後に始める。
 * 同じ一覧で Skill の「編集」か「新しい Skill を作る」を選ぶと、Skill の編集の依頼になる（design.md §11.6）。
 * 編集の依頼は別の Run で行うので、使う Skill とは一緒に選べず、実行中なら今の作業の後に始める。
 * 使う Connector は、ボタンか、行の始めか空白の後で `@` を打つと開く一覧から選ぶ（harness.md §10.2）。使える
 * Connector の一覧も依頼を始めるときに決めるので、実行中なら今の作業の後に始める。
 */
const props = defineProps<{
  activeRun?: Run | null
  /** 回答待ちのエージェントからの質問がある */
  hasOpenQuestion?: boolean
  attachments?: PendingAttachment[]
  /** 指定できる Skill */
  skills?: Skill[]
  /** 編集できる Skill */
  editableSkills?: Skill[]
  /** 指定できる Connector */
  connectors?: AppConnector[]
  /** 個人情報・機密情報を扱う会話（機密の Connector を選べる） */
  sensitive?: boolean
  sending?: boolean
  stopping?: boolean
  disabled?: boolean
}>()
const text = defineModel<string>({ required: true })
const mode = defineModel<string>('mode', { default: MessageMode.AUTO })
const selectedSkills = defineModel<Skill[]>('selectedSkills', { default: () => [] })
const skillEdit = defineModel<SkillEditChoice | null>('skillEdit', { default: null })
const selectedConnectors = defineModel<AppConnector[]>('selectedConnectors', { default: () => [] })
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

const skillsFull = computed(() => selectedSkills.value.length >= MAX_CHOSEN_SKILLS)
const skillPickerOpen = ref(false)
const skillSearch = ref('')
const connectorsFull = computed(() => selectedConnectors.value.length >= MAX_CHOSEN_CONNECTORS)
const connectorPickerOpen = ref(false)
const connectorSearch = ref('')
// 行の始めか空白の後に `/`・`@` を打ったら、その文字を消して Skill・Connector の一覧を開く
watch(text, (value, old) => {
  if (props.disabled || value.length !== (old?.length ?? 0) + 1) return
  const trigger = /(^|\s)([/@])$/.exec(value)?.[2]
  if (!trigger) return
  text.value = value.slice(0, -1)
  if (trigger === '/') {
    skillSearch.value = ''
    skillPickerOpen.value = true
  } else {
    connectorSearch.value = ''
    connectorPickerOpen.value = true
  }
})
const selectSkill = (skill: Skill) => {
  selectedSkills.value = [...selectedSkills.value, skill]
  skillEdit.value = null
}
const chooseEdit = (skill: Skill | null) => {
  skillEdit.value = { skill }
  selectedSkills.value = []
}
const removeSkill = (id: string) => {
  selectedSkills.value = selectedSkills.value.filter((s) => s.id !== id)
}
const selectConnector = (connector: AppConnector) => {
  selectedConnectors.value = [...selectedConnectors.value, connector]
}
const removeConnector = (id: string) => {
  selectedConnectors.value = selectedConnectors.value.filter((c) => c.id !== id)
}
// Skill・Connector を指定した依頼は、実行中なら今の作業の後に始める
const separateRequest = computed(
  () => selectedSkills.value.length > 0 || selectedConnectors.value.length > 0 || skillEdit.value !== null,
)

const attachmentsReady = computed(() => (props.attachments ?? []).every((a) => a.artifact))
// Skill の編集の依頼では、どう変えるかの本文が要る
const hasContent = computed(() =>
  skillEdit.value
    ? Boolean(text.value.trim())
    : Boolean(text.value.trim()) || Boolean(props.attachments?.length) || selectedSkills.value.length > 0,
)

const modeItems = computed(() => [
  { value: MessageMode.AUTO, title: props.hasOpenQuestion ? t('回答として送る') : t('今の作業に追加') },
  { value: MessageMode.NEXT, title: t('次の依頼にする') },
])

const placeholder = computed(() => {
  if (skillEdit.value?.skill) return t('Skillをどう変えるかを入力してください（添付したファイルを資料にもできます）')
  if (skillEdit.value) return t('どんなSkillを作るかを入力してください（この会話の手順をSkillにする等）')
  if (props.activeRun && selectedSkills.value.length)
    return t('今の作業が終わった後に、Skillを使って始める依頼を入力してください')
  if (props.activeRun && selectedConnectors.value.length)
    return t('今の作業が終わった後に、Connectorを使って始める依頼を入力してください')
  // `@` は vue-i18n のリンクの記法なので、文字として出すには {'@'} と書く
  if (!props.activeRun) return t("依頼を入力してください（/ でSkill、{'@'} でConnectorを指定）")
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
    <template v-if="attachments?.length || selectedSkills.length || selectedConnectors.length || skillEdit" #header>
      <div class="grid gap-2">
        <div v-if="skillEdit" class="flex flex-wrap items-center gap-2">
          <UBadge
            color="warning"
            variant="subtle"
            :icon="skillEdit.skill ? 'i-lucide-pencil' : 'i-lucide-plus'"
            class="gap-1"
          >
            {{ skillEdit.skill ? $t('Skillの編集：{name}', { name: skillEdit.skill.name }) : $t('新しいSkillを作る') }}
            <DBtn
              icon="i-lucide-x"
              size="xs"
              variant="link"
              color="neutral"
              class="-me-1 p-0.5"
              :aria-label="$t('Skillの編集をやめる')"
              @click="skillEdit = null"
            />
          </UBadge>
          <span class="text-xs text-muted">{{
            activeRun ? $t('今の作業の後に、別の作業として行います') : $t('別の作業として行います')
          }}</span>
        </div>
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
        <ul v-if="selectedConnectors.length" class="flex flex-wrap gap-2">
          <li v-for="connector in selectedConnectors" :key="connector.id">
            <UBadge
              color="info"
              variant="subtle"
              :icon="connector.isSensitive ? 'i-lucide-shield' : 'i-lucide-database'"
              class="gap-1"
            >
              {{ connector.name }}
              <DBtn
                icon="i-lucide-x"
                size="xs"
                variant="link"
                color="neutral"
                class="-me-1 p-0.5"
                :aria-label="$t('Connectorの指定を外す')"
                @click="removeConnector(connector.id)"
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
          v-model:open="skillPickerOpen"
          v-model:search-term="skillSearch"
          :skills="skills ?? []"
          :editable="editableSkills ?? []"
          :selected="selectedSkills.map((s) => s.id)"
          :sensitive="sensitive ?? false"
          :full="skillsFull"
          :disabled="disabled"
          @select="selectSkill"
          @edit="chooseEdit"
          @create="chooseEdit(null)"
        />
        <ConversationConnectorPicker
          v-model:open="connectorPickerOpen"
          v-model:search-term="connectorSearch"
          :connectors="connectors ?? []"
          :selected="selectedConnectors.map((c) => c.id)"
          :sensitive="sensitive ?? false"
          :full="connectorsFull"
          :disabled="disabled"
          @select="selectConnector"
        />
        <DSelectButton
          v-if="activeRun && !separateRequest"
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
