<script setup lang="ts">
/**
 * エージェントからの確認（ask_user）、モデルにつながらないときの切り替えの確認、Skill の編集の提案の確認
 * （request_skill_edit。design.md §11.6）に答える。
 * 質問ごとに選択肢から選ぶか自由記述で答え、全体の補足も書ける。回答を受け付けると Run が再開する。
 */
const props = defineProps<{ runId: string; clarification: Clarification }>()
const emit = defineEmits<{ answered: [value: Clarification] }>()
const toast = useToast()
const { t } = useI18n()
const api = useAPI()

const isModelSwitch = computed(() => props.clarification.kind === ClarificationKind.MODEL_SWITCH)
const isSkillEdit = computed(() => props.clarification.kind === ClarificationKind.SKILL_EDIT)
// 選択肢から選ぶ確認（自由記述の回答欄を出さない）
const choiceOnly = computed(() => isModelSwitch.value || isSkillEdit.value)

const emptyForm = (): ClarificationAnswer => ({
  answers: props.clarification.questions.map(() => ({ selected: [], text: '' })),
  comment: '',
})
const form = ref(emptyForm())
watch(
  () => props.clarification.id,
  () => (form.value = emptyForm()),
)

const answerAt = (index: number) => form.value.answers![index]!
const optionItems = (question: Clarification['questions'][number]) =>
  question.options.map((o) => ({ value: o.label, title: o.label, description: o.description || undefined }))

const { onSubmit, loading, errors } = createSubmit({
  form,
  fn: (data) => api.runs(props.runId).clarifications(props.clarification.id).answer(data),
  onSuccess: (result) => {
    emit('answered', result)
    toast.add({ title: t('回答しました。作業を再開します'), color: 'success' })
  },
  // 409（別のタブや外部のスレッドから先に回答された）も、DFormAlert に表示する
  onError: () => {},
})

// 回答の行ごとのエラーは、質問の欄ではなく先頭にまとめて出す
const alertErrors = computed<ValidationErrors>(() => ({
  nonFieldErrors: [...(errors.value.nonFieldErrors ?? []), ...flattenRowErrors(errors.value.answers)],
}))

const canPost = computed(() =>
  isSkillEdit.value
    ? form.value.answers!.some((a) => (a.selected ?? []).length > 0)
    : form.value.answers!.some((a) => (a.selected ?? []).length > 0 || a.text?.trim()) ||
      Boolean(form.value.comment?.trim()),
)
</script>

<template>
  <UCard :ui="{ body: 'grid gap-4' }">
    <DFormAlert :errors="alertErrors" />
    <div v-for="(question, index) in clarification.questions" :key="index" class="grid gap-2">
      <div class="flex items-center gap-2">
        <UBadge v-if="question.header" color="neutral" variant="subtle">{{ question.header }}</UBadge>
        <p class="text-sm font-medium whitespace-pre-wrap">{{ question.question }}</p>
      </div>
      <DCheckboxGroup
        v-if="question.multiSelect"
        v-model="answerAt(index).selected"
        :label="question.question"
        :show-label="false"
        :items="optionItems(question)"
        variant="card"
      />
      <DRadioGroup
        v-else
        :model-value="answerAt(index).selected?.[0] ?? null"
        :label="question.question"
        :show-label="false"
        :items="optionItems(question)"
        variant="card"
        @update:model-value="(v) => (answerAt(index).selected = v === null || v === undefined ? [] : [String(v)])"
      />
      <ULink
        v-if="isSkillEdit && clarification.target?.skill"
        :to="`/skills/${clarification.target.skill}`"
        target="_blank"
        class="text-xs"
      >
        {{ $t('Skillの今の内容を見る') }}
      </ULink>
      <DTextField
        v-if="!choiceOnly"
        v-model="answerAt(index).text"
        :label="$t('自由記述')"
        :show-label="false"
        :placeholder="$t('選択肢にない場合は、ここに書いてください')"
      />
    </div>
    <DTextarea
      v-if="!isModelSwitch"
      v-model="form.comment"
      :label="$t('補足（任意）')"
      :error-messages="errors.comment"
    />
    <div class="flex justify-end">
      <DBtn :disabled="!canPost" :loading="loading" @click="onSubmit">{{ $t('回答する') }}</DBtn>
    </div>
  </UCard>
</template>
