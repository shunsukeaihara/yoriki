<script setup lang="ts">
/** Draftの評価の履歴。新しいものから並べる。評価中のものがあれば、終わるまで読み直す */
const props = defineProps<{ skillId: string }>()
const reloadKey = defineModel<number>('reloadKey', { default: 0 })
const api = useAPI()
const toast = useToast()
const { t } = useI18n()

const {
  items: evaluations,
  loading,
  fetchAll,
} = useFetchAll(() => api.skills(props.skillId).evaluations().list({ pageSize: 50 }))
const load = () => fetchAll().catch((err) => toast.add({ title: makeErrorMsg(err), color: 'error' }))
watch(reloadKey, load, { immediate: true })

const running = computed(() => evaluations.value.some((e) => e.status === EvaluationStatus.RUNNING))
let timer: ReturnType<typeof setInterval> | null = null
watch(
  running,
  (value) => {
    if (timer) clearInterval(timer)
    timer = value ? setInterval(load, 5000) : null
  },
  { immediate: true },
)
onBeforeUnmount(() => timer && clearInterval(timer))
</script>

<template>
  <DFormSection :title="$t('評価の履歴')">
    <div class="grid gap-2">
      <p v-if="loading && !evaluations.length" class="text-sm text-muted">{{ $t('読み込み中') }}</p>
      <p v-else-if="!evaluations.length" class="text-sm text-muted">{{ $t('まだ評価していません') }}</p>
      <NuxtLink
        v-for="e in evaluations"
        :key="e.id"
        :to="`/skills/${skillId}/evaluations/${e.id}`"
        class="flex flex-wrap items-center gap-x-3 gap-y-1 rounded-md border border-default px-3 py-2 hover:bg-elevated/50"
      >
        <UBadge :color="evaluationStatusColor(e.status)" variant="subtle" size="sm">{{ e.statusDisplay }}</UBadge>
        <span class="text-sm font-medium">{{ evaluationSummary(e, t) }}</span>
        <UBadge v-if="e.current" color="primary" variant="subtle" size="sm">{{ $t('今のDraft') }}</UBadge>
        <span v-else class="text-xs text-muted">{{ $t('前のDraft（revision {r}）', { r: e.draftRevision }) }}</span>
        <UBadge v-if="e.fromConversation" color="neutral" variant="subtle" size="sm" icon="i-lucide-message-square">
          {{ $t('会話から') }}
        </UBadge>
        <div class="flex-1" />
        <span class="text-xs text-muted">
          {{ e.modelProfileName }}・{{ formatDatetime(e.created) }}・{{
            e.requestedByInfo.displayName || e.requestedByInfo.email
          }}
        </span>
        <UIcon name="i-lucide-chevron-right" class="size-4 text-dimmed" />
      </NuxtLink>
    </div>
  </DFormSection>
</template>
