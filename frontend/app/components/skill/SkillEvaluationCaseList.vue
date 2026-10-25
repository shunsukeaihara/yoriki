<script setup lang="ts">
/**
 * 評価ケースの一覧。追加・変更・削除はDraftを編集できる人。公開の前に必須のケースの変更・削除は公開の権限が要る
 * （ボタンを隠すだけで、サーバーでも確かめる）
 */
const props = defineProps<{ skill: Skill }>()
const cases = defineModel<SkillEvaluationCase[]>({ required: true })
const api = useAPI()
const toast = useToast()
const confirm = useConfirmDialog()
const { t } = useI18n()

const canChange = (item: SkillEvaluationCase) => !item.required || props.skill.access >= SkillAccess.PUBLISH

const sorted = (items: SkillEvaluationCase[]) =>
  [...items].sort((a, b) => Number(b.required ?? false) - Number(a.required ?? false) || a.name.localeCompare(b.name))

const onSaved = (value: SkillEvaluationCase) => {
  cases.value = sorted([...cases.value.filter((c) => c.id !== value.id), value])
}

const onDelete = async (item: SkillEvaluationCase) => {
  const ok = await confirm({
    title: t('評価ケース「{name}」を削除しますか？', { name: item.name }),
    description: t('これまでの評価の結果は残ります'),
    confirmLabel: t('削除'),
    color: 'error',
  })
  if (!ok) return
  try {
    await api.skills(props.skill.id).evaluationCases(item.id).delete()
    cases.value = cases.value.filter((c) => c.id !== item.id)
    toast.add({ title: t('削除しました'), color: 'success' })
  } catch (err) {
    toast.add({ title: t('エラー'), description: makeErrorMsg(err), color: 'error' })
  }
}
</script>

<template>
  <DFormSection
    :title="$t('評価ケース')"
    :description="$t('業務の依頼の例です。Draftを評価するとき、公開中の版とDraftで同じ依頼を試して結果を比べます')"
  >
    <div class="grid gap-2">
      <p v-if="!cases.length" class="text-sm text-muted">{{ $t('評価ケースはありません') }}</p>
      <div
        v-for="item in cases"
        :key="item.id"
        class="flex flex-wrap items-start gap-x-3 gap-y-1 rounded-md border border-default px-3 py-2"
      >
        <div class="grid min-w-0 flex-1 gap-1">
          <div class="flex flex-wrap items-center gap-2">
            <span class="text-sm font-semibold">{{ item.name }}</span>
            <UBadge v-if="item.required" color="warning" variant="subtle" size="sm" icon="i-lucide-shield-check">
              {{ $t('公開の前に必須') }}
            </UBadge>
            <UBadge v-if="item.expectedResult != null" color="neutral" variant="subtle" size="sm">
              {{ $t('期待する業務結果あり') }}
            </UBadge>
          </div>
          <p class="line-clamp-2 text-sm whitespace-pre-wrap text-muted">{{ item.prompt }}</p>
          <div v-if="item.filesInfo.length" class="flex flex-wrap gap-x-4 gap-y-1">
            <SkillFileLink v-for="file in item.filesInfo" :key="file.id" :skill-id="skill.id" :file="file" />
          </div>
        </div>
        <div v-if="canChange(item)" class="flex gap-1">
          <SkillEvaluationCaseDialog :skill="skill" :evaluation-case="item" @saved="onSaved">
            <template #trigger="{ on }">
              <DBtn size="sm" color="neutral" variant="ghost" icon="i-lucide-pencil" v-on="on">{{ $t('変更') }}</DBtn>
            </template>
          </SkillEvaluationCaseDialog>
          <DBtn
            size="sm"
            color="error"
            variant="ghost"
            icon="i-lucide-trash-2"
            :aria-label="$t('削除')"
            @click="onDelete(item)"
          />
        </div>
      </div>
      <div>
        <SkillEvaluationCaseDialog :skill="skill" @saved="onSaved">
          <template #trigger="{ on }">
            <DBtn icon="i-lucide-plus" color="neutral" variant="outline" v-on="on">{{ $t('評価ケースを追加') }}</DBtn>
          </template>
        </SkillEvaluationCaseDialog>
      </div>
    </div>
  </DFormSection>
</template>
