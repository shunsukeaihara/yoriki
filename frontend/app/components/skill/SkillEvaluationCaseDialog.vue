<script setup lang="ts">
/**
 * 評価ケースの作成と変更（design.md §3.7.1）。依頼の文と添付が評価のRunの最初の発言になり、確認する観点はLLMの判定に、
 * 期待する業務結果は submit_result の値との照合に使う。
 * 「公開の前に必須」は部門・全社のSkillで公開の権限を持つ人だけが付けられる（サーバーでも確かめ、403になる）
 */
import DDialog from '../common/DDialog.vue'

const props = defineProps<{ skill: Skill; evaluationCase?: SkillEvaluationCase | null }>()
const emit = defineEmits<{ saved: [value: SkillEvaluationCase] }>()
const api = useAPI()
const toast = useToast()
const { t } = useI18n()
const dialog = ref<InstanceType<typeof DDialog> | null>(null)

const isEdit = computed(() => Boolean(props.evaluationCase))
// 必須の印を付けられるのは、部門・全社のSkillで公開の権限を持つ人
const canRequire = computed(() => props.skill.scope !== SkillScope.PERSONAL && props.skill.canPublish)

const emptyForm = () => ({
  name: '',
  prompt: '',
  criteria: '',
  expectedText: '',
  required: false,
  newFiles: null as File | File[] | null,
  removeFiles: [] as string[],
})
const form = ref(emptyForm())
const expectedError = ref<string | null>(null)

const onOpen = (open: boolean) => {
  if (!open) return
  const value = props.evaluationCase
  form.value = value
    ? {
        name: value.name,
        prompt: value.prompt,
        criteria: value.criteria ?? '',
        expectedText: schemaToText(value.expectedResult),
        required: value.required ?? false,
        newFiles: null,
        removeFiles: [],
      }
    : emptyForm()
  errors.value = {}
  expectedError.value = null
}

const keptFiles = computed(() =>
  (props.evaluationCase?.filesInfo ?? []).filter((f) => !form.value.removeFiles.includes(f.id)),
)
const toggleRemove = (fileId: string) => {
  const removing = form.value.removeFiles
  form.value.removeFiles = removing.includes(fileId) ? removing.filter((id) => id !== fileId) : [...removing, fileId]
}

const { onSubmit, loading, errors } = createSubmit({
  form,
  fn: ({ name, prompt, criteria, expectedText, required, newFiles, removeFiles }) => {
    const expected = parseSchemaText(expectedText, t)
    expectedError.value = expected.error
    if (expected.error) throw new Error(expected.error)
    const addFiles = newFiles ? (Array.isArray(newFiles) ? newFiles : [newFiles]) : []
    const data = { name: name.trim(), prompt, criteria, expectedResult: expected.value, addFiles, removeFiles }
    const cases = api.skills(props.skill.id).evaluationCases
    // 必須の印は、付けられる人だけが送る（送らなければ変えない）
    return isEdit.value
      ? cases(props.evaluationCase!.id).patch(canRequire.value ? { ...data, required } : data)
      : cases().create(canRequire.value ? { ...data, required } : data)
  },
  onSuccess: (result) => {
    emit('saved', result)
    dialog.value?.closeDialog()
    toast.add({ title: t('保存しました'), color: 'success' })
  },
  onError: (err) => {
    if (!expectedError.value) toast.add({ title: t('エラー'), description: makeErrorMsg(err), color: 'error' })
  },
})

const canPost = computed(() => Boolean(form.value.name.trim() && form.value.prompt.trim()))
</script>

<template>
  <DDialog ref="dialog" @update:open="onOpen">
    <template #trigger="{ on }">
      <slot name="trigger" :on="on" />
    </template>
    <template #header>{{ isEdit ? $t('評価ケースを変更') : $t('評価ケースを追加') }}</template>
    <template #content>
      <DFormStack>
        <DFormAlert :errors="errors" />
        <DTextField v-model="form.name" :label="$t('名前')" required :error-messages="errors.name" />
        <DTextarea
          v-model="form.prompt"
          :label="$t('依頼の文')"
          required
          :rows="4"
          :hint="$t('業務の依頼として送る文です。公開中の版とDraftで同じ依頼を試します')"
          :error-messages="errors.prompt"
        />
        <div class="grid gap-2">
          <p class="text-sm font-medium">{{ $t('添付') }}</p>
          <div v-for="file in evaluationCase?.filesInfo ?? []" :key="file.id" class="flex items-center gap-2 text-sm">
            <SkillFileLink
              :skill-id="skill.id"
              :file="file"
              :class="{ 'line-through opacity-60': form.removeFiles.includes(file.id) }"
            />
            <DBtn size="xs" color="neutral" variant="ghost" @click="toggleRemove(file.id)">
              {{ form.removeFiles.includes(file.id) ? $t('外すのをやめる') : $t('外す') }}
            </DBtn>
          </div>
          <DFileInputField
            v-model="form.newFiles"
            :label="$t('加えるファイル')"
            multiple
            :preview="false"
            :hint="$t('添付は{limit}個までです（今は{count}個）', { limit: 5, count: keptFiles.length })"
            :error-messages="errors.addFiles"
          />
        </div>
        <DTextarea
          v-model="form.criteria"
          :label="$t('確認する観点')"
          :rows="3"
          :hint="$t('LLMが公開中の版とDraftの結果を比べるときに見る点です（例：合計金額の照合の誤りがない）')"
          :error-messages="errors.criteria"
        />
        <DTextarea
          v-model="form.expectedText"
          :label="$t('期待する業務結果（JSON）')"
          :rows="4"
          class="font-mono"
          :hint="$t('書いた項目だけを、業務結果と一致するか確かめます。空なら確かめません')"
          :error-messages="expectedError ? [expectedError] : errors.expectedResult"
        />
        <DCheckbox
          v-if="canRequire"
          v-model="form.required"
          :label="$t('公開の前に必須')"
          :hint="$t('今のDraftでこのケースが通過するまで、公開できなくなります')"
          :error-messages="errors.required"
        />
      </DFormStack>
    </template>
    <template #footer="{ closeDialog }">
      <DBtn color="neutral" variant="ghost" @click="closeDialog()">{{ $t('キャンセル') }}</DBtn>
      <DBtn :disabled="!canPost" :loading="loading" @click="onSubmit">{{ isEdit ? $t('更新') : $t('追加') }}</DBtn>
    </template>
  </DDialog>
</template>
