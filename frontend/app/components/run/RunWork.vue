<script setup lang="ts">
/**
 * 会話の中の1つの Run の作業（design.md §11.2「現在の作業」）。状態、作業の計画、作業の記録、確認への回答、
 * 書きかけの応答を出す。実行中の Run はストリームのイベント（events・delta）を受け取り、終わった Run は記録を
 * 開いたときに API から読む。
 */
const props = defineProps<{
  run: Run
  /** ストリームで受け取ったイベント。渡さなければ、記録を開いたときに API から読む */
  events?: RunStreamEvent[]
  /** 書きかけの応答（LLM の出力の差分） */
  delta?: string
  /** 回答待ちの確認 */
  clarifications?: Clarification[]
}>()
const emit = defineEmits<{ answered: [value: Clarification] }>()
const api = useAPI()

const active = computed(() => isActiveRun(props.run))

const open = ref(false)
const {
  items: loadedEvents,
  loading,
  fetchAll,
} = useFetchAll(() => api.runs(props.run.id).events().list({ pageSize: 200 }))
watch(open, (value) => {
  if (value && !props.events) fetchAll()
})
const events = computed(() => props.events ?? loadedEvents.value)

// 最後の応答は、Run が終わって会話のメッセージになるまでここに出す
const pendingReply = computed(() => {
  if (!active.value) return ''
  const last = events.value.findLast((e) => e.type === 'run.message' && e.kind === RunEventKind.ASSISTANT_MESSAGE)
  return last?.type === 'run.message' && last.toolCalls.length === 0 ? last.text : ''
})
const streamingText = computed(() => props.delta || pendingReply.value)

const formattedResult = computed(() => (props.run.result == null ? '' : JSON.stringify(props.run.result, null, 2)))
</script>

<template>
  <div class="grid gap-3">
    <div class="flex flex-wrap items-center gap-2 text-sm">
      <RunStatusBadge :run="run" />
      <UChatShimmer v-if="run.status === RunStatus.RUNNING && !streamingText" :text="$t('作業しています')" />
      <span class="text-xs text-dimmed"
        >{{ run.modelProfileName }}<template v-if="run.effort"> · {{ run.effort }}</template></span
      >
    </div>

    <UAlert
      v-if="run.status === RunStatus.FAILED"
      color="error"
      variant="subtle"
      icon="i-lucide-circle-x"
      :title="run.endReasonDisplay || run.statusDisplay"
      :description="run.error || undefined"
    />

    <!-- 使っている Skill と版（`/` で指定したものと、エージェントが読み込んだもの。design.md §11.8） -->
    <ul v-if="run.skills.length" class="flex flex-wrap gap-1">
      <li v-for="skill in run.skills" :key="skill.skill">
        <UTooltip :text="skill.chosen ? $t('指定したSkill') : $t('エージェントが選んだSkill')">
          <UBadge :variant="skill.chosen ? 'subtle' : 'outline'" color="primary" size="sm" icon="i-lucide-sparkles">
            {{ $t('{name}（第{number}版）', { name: skill.name, number: skill.number }) }}
          </UBadge>
        </UTooltip>
      </li>
    </ul>

    <RunTodoList v-if="run.todos.length" :todos="run.todos" />

    <RunEventLog v-if="active" :events="events" active />
    <UCollapsible v-else v-model:open="open">
      <DBtn
        variant="link"
        color="neutral"
        size="sm"
        class="px-0"
        :trailing-icon="open ? 'i-lucide-chevron-up' : 'i-lucide-chevron-down'"
      >
        {{ $t('作業の記録') }}
      </DBtn>
      <template #content>
        <div class="mt-2 border-l-2 border-default pl-3">
          <USkeleton v-if="loading && !events.length" class="h-6 w-48" />
          <RunEventLog v-else :events="events" />
        </div>
      </template>
    </UCollapsible>

    <MarkdownContent v-if="streamingText" :text="streamingText" />

    <UCollapsible v-if="formattedResult">
      <DBtn variant="link" color="neutral" size="sm" class="px-0" trailing-icon="i-lucide-chevron-down">
        {{ $t('業務結果') }}
      </DBtn>
      <template #content>
        <pre class="mt-2 max-h-80 overflow-auto rounded-md bg-muted p-2 text-xs whitespace-pre-wrap">{{
          formattedResult
        }}</pre>
      </template>
    </UCollapsible>

    <RunClarificationForm
      v-for="clarification in clarifications"
      :key="clarification.id"
      :run-id="run.id"
      :clarification="clarification"
      @answered="emit('answered', $event)"
    />
  </div>
</template>
