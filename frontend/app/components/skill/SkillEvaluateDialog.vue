<script setup lang="ts">
/**
 * 今のDraftを評価する（design.md §3.7.1）。選んだケースごとに、公開中の版とDraftで評価のRunを1つずつ動かす。
 * モデルは評価のたびに選ぶ。利用量は自分の実行として記録する
 */
import DDialog from '../common/DDialog.vue'

const props = defineProps<{ skill: Skill; cases: SkillEvaluationCase[] }>()
const emit = defineEmits<{ started: [value: SkillEvaluation] }>()
const api = useAPI()
const toast = useToast()
const { t } = useI18n()
const dialog = ref<InstanceType<typeof DDialog> | null>(null)

const models = ref<AppModelProfile[]>([])
const form = ref({ cases: [] as string[], modelProfile: '' })

const onOpen = async (open: boolean) => {
  if (!open) return
  errors.value = {}
  // 必須のケースを先に、上限まで選んでおく（一覧は必須が先に並ぶ）
  form.value = { cases: props.cases.slice(0, MAX_CASES_PER_EVALUATION).map((c) => c.id), modelProfile: '' }
  try {
    models.value = (await api.modelProfiles.list()).filter((m) => m.supportsTools)
    form.value.modelProfile = (models.value.find((m) => m.isDefault) ?? models.value[0])?.id ?? ''
  } catch (err) {
    toast.add({ title: t('エラー'), description: makeErrorMsg(err), color: 'error' })
  }
}

const caseItems = computed(() =>
  props.cases.map((c) => ({ value: c.id, title: c.required ? `${c.name}（${t('必須')}）` : c.name })),
)
const modelItems = computed(() => models.value.map((m) => ({ value: m.id, title: m.name })))

const { onSubmit, loading, errors } = createSubmit({
  form,
  fn: (data) => api.skills(props.skill.id).evaluations().create(data),
  onSuccess: (result) => {
    emit('started', result)
    dialog.value?.closeDialog()
    toast.add({ title: t('評価を始めました。数分かかります'), color: 'success' })
  },
  onError: (err) => toast.add({ title: t('エラー'), description: makeErrorMsg(err), color: 'error' }),
})

const tooMany = computed(() => form.value.cases.length > MAX_CASES_PER_EVALUATION)
const canPost = computed(() => form.value.cases.length > 0 && !tooMany.value && Boolean(form.value.modelProfile))
</script>

<template>
  <DDialog ref="dialog" @update:open="onOpen">
    <template #trigger="{ on }">
      <slot name="trigger" :on="on" />
    </template>
    <template #header>{{ $t('Draftを評価') }}</template>
    <template #content>
      <DFormStack>
        <DFormAlert :errors="errors" />
        <p class="text-sm text-muted">
          {{
            skill.version
              ? $t('選んだケースごとに、公開中の版（版 {number}）とDraftで同じ依頼を試して結果を比べます', {
                  number: skill.version,
                })
              : $t('まだ公開していないので、Draftだけで試します')
          }}
        </p>
        <DCheckboxGroup
          v-model="form.cases"
          :label="$t('評価ケース')"
          :items="caseItems"
          :hint="$t('1回に{limit}件まで選べます', { limit: MAX_CASES_PER_EVALUATION })"
          :error-messages="
            tooMany ? [$t('{limit}件までにしてください', { limit: MAX_CASES_PER_EVALUATION })] : errors.cases
          "
        />
        <DSelect
          v-model="form.modelProfile"
          :label="$t('モデル')"
          :items="modelItems"
          required
          :hint="$t('評価のRunと、結果を比べる判定に使います')"
          :error-messages="errors.modelProfile"
        />
      </DFormStack>
    </template>
    <template #footer="{ closeDialog }">
      <DBtn color="neutral" variant="ghost" @click="closeDialog()">{{ $t('キャンセル') }}</DBtn>
      <DBtn icon="i-lucide-flask-conical" :disabled="!canPost" :loading="loading" @click="onSubmit">
        {{ $t('評価する') }}
      </DBtn>
    </template>
  </DDialog>
</template>
