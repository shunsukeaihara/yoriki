<script setup lang="ts">
/**
 * Draftの評価の結果（design.md §3.7.1）。ケースごとに公開中の版とDraftの結果を並べる。評価中は5秒ごとに読み直す。
 * Draftを編集できる人だけが見られる（サーバーでも確かめ、403になる）
 */
const route = useRoute()
const api = useAPI()
const toast = useToast()
const confirm = useConfirmDialog()
const { t } = useI18n()
const skillId = route.params.skillId as string
const evaluationId = route.params.evaluationId as string

const [{ data: skill, error: skillError }, { data: evaluation, error, refresh }] = await Promise.all([
  useAsyncData(`skill-${skillId}`, () => api.skills(skillId).get()),
  useAsyncData(`skill-evaluation-${evaluationId}`, () => api.skills(skillId).evaluations(evaluationId).get()),
])
if (skillError.value) throw createError(skillError.value)
if (error.value) throw createError(error.value)

const running = computed(() => evaluation.value?.status === EvaluationStatus.RUNNING)
let timer: ReturnType<typeof setInterval> | null = null
watch(
  running,
  (value) => {
    if (timer) clearInterval(timer)
    timer = value ? setInterval(() => refresh(), 5000) : null
  },
  { immediate: true },
)
onBeforeUnmount(() => timer && clearInterval(timer))

const cancelling = ref(false)
const onCancel = async () => {
  const ok = await confirm({
    title: t('評価を止めますか？'),
    description: t('動いている評価の実行を止めます。終わっていないケースは通過しなかったことになります'),
    confirmLabel: t('止める'),
    color: 'error',
  })
  if (!ok) return
  cancelling.value = true
  try {
    evaluation.value = await api.skills(skillId).evaluations(evaluationId).cancel()
  } catch (err) {
    toast.add({ title: t('エラー'), description: makeErrorMsg(err), color: 'error' })
  } finally {
    cancelling.value = false
  }
}
</script>

<template>
  <UDashboardPanel :id="`skill-evaluation-${evaluationId}`">
    <template #header>
      <UDashboardNavbar :title="$t('Draftの評価')">
        <template #leading>
          <DBtn
            :to="`/skills/${skillId}?tab=evaluation`"
            color="neutral"
            variant="ghost"
            icon="i-lucide-arrow-left"
            :aria-label="$t('Skillへ')"
          />
        </template>
        <template #right>
          <DBtn
            v-if="running"
            color="error"
            variant="ghost"
            icon="i-lucide-square"
            :loading="cancelling"
            @click="onCancel"
          >
            {{ $t('評価を止める') }}
          </DBtn>
        </template>
      </UDashboardNavbar>
    </template>

    <template #body>
      <UContainer v-if="evaluation && skill" class="grid max-w-5xl min-w-0 gap-4">
        <div class="grid gap-2">
          <div class="flex flex-wrap items-center gap-2">
            <span class="font-semibold">{{ skill.name }}</span>
            <UBadge :color="evaluationStatusColor(evaluation.status)" variant="subtle">
              {{ evaluation.statusDisplay }}
            </UBadge>
            <span class="text-sm font-medium">{{ evaluationSummary(evaluation, t) }}</span>
          </div>
          <p class="text-xs text-muted">
            {{
              evaluation.baseVersion
                ? $t('公開中の版 {base} と Draft（revision {r}）を比べました', {
                    base: evaluation.baseVersion,
                    r: evaluation.draftRevision,
                  })
                : $t('Draft（revision {r}）だけで試しました', { r: evaluation.draftRevision })
            }}・{{ evaluation.modelProfileName }}・{{ formatDatetime(evaluation.created) }}・{{
              evaluation.requestedByInfo.displayName || evaluation.requestedByInfo.email
            }}
          </p>
          <UAlert
            v-if="!evaluation.current"
            color="neutral"
            variant="subtle"
            icon="i-lucide-history"
            :title="$t('この評価の後にDraftが変わったか、公開されました。今のDraftの評価ではありません')"
          />
          <p class="text-xs text-muted">
            {{
              $t(
                '「通過」はDraftの側が完了し、業務結果と期待する値を満たしたかで決まります。LLMの判定は参考です。正しさは結果を読んで確かめてください',
              )
            }}
          </p>
        </div>
        <SkillEvaluationResultCard
          v-for="result in evaluation.results"
          :key="result.order"
          :result="result"
          :has-base="evaluation.baseVersion != null"
        />
      </UContainer>
    </template>
  </UDashboardPanel>
</template>
