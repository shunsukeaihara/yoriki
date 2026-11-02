<script setup lang="ts">
/**
 * 会話の中の1つの Run の作業（design.md §11.2「現在の作業」）。状態、作業の計画、作業の記録、確認への回答、
 * 書きかけの応答を出す。実行中の Run はストリームのイベント（events・delta）を受け取り、終わった Run は記録を
 * 開いたときに API から読む。
 */
const props = withDefaults(
  defineProps<{
    run: Run
    /** 作業の記録を出すか。結果だけを見る人（チャットから起動した人。authorization.md §7.1）には出さない */
    showLog?: boolean
    /** ストリームで受け取ったイベント。渡さなければ、記録を開いたときに API から読む */
    events?: RunStreamEvent[]
    /** 書きかけの応答（LLM の出力の差分） */
    delta?: string
    /** 回答待ちの確認 */
    clarifications?: Clarification[]
  }>(),
  { showLog: true, events: undefined, delta: undefined, clarifications: undefined },
)
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

// Skill のスクリプトからの Connector の呼出し。モデルの文脈（イベント）に入らないので、別に読む。実行中は、
// 呼出しを処理しうる間（shell・job_output の終わりを待つ間）に読み直し、Tool の結果が届いたら最後に読む。
// 読み直すのは、まだ終わっていない呼出し（なければ最後の呼出し）から後だけ（1 Run で 1,000 回まで呼べるため）
const scriptCalls = ref<ScriptCall[]>([])
const UNFINISHED: ToolStatusEnum[] = [ToolStatus.REQUESTED, ToolStatus.RUNNING]
const fetchScriptCalls = async () => {
  const known = scriptCalls.value
  const since = (known.find((c) => UNFINISHED.includes(c.status)) ?? known.at(-1))?.created
  let page = await api
    .runs(props.run.id)
    .scriptCalls()
    .list({ pageSize: 200, ...(since ? { since } : {}) })
  let fresh = page.results
  while (page.next) {
    page = await api.http.get<Paginated<ScriptCall>, Paginated<ScriptCall>>(page.next)
    fresh = fresh.concat(page.results)
  }
  // since 以後のものはすべて読み直したので、手元のものは読み直さなかった分（since より前）だけを残す。
  // 時刻はミリ秒より細かいので、比べずに ID で除く
  const ids = new Set(fresh.map((c) => c.id))
  scriptCalls.value = since ? [...known.filter((c) => !ids.has(c.id)), ...fresh] : fresh
}
// 読んでいる間に求められたら、読み終えてからもう一度読む（Run の終わりの後の呼出しを取りこぼさない）
let loadingScriptCalls = false
let refreshAgain = false
// 読めなかったら、記録の中に「読み直す」を出す（Run が終わった後は、ほかに読み直す機会がない）
const scriptCallsFailed = ref(false)
const refreshScriptCalls = async () => {
  if (!props.showLog) return
  if (loadingScriptCalls) {
    refreshAgain = true
    return
  }
  loadingScriptCalls = true
  try {
    do {
      refreshAgain = false
      await fetchScriptCalls()
    } while (refreshAgain)
    scriptCallsFailed.value = false
  } catch {
    scriptCallsFailed.value = true
  } finally {
    loadingScriptCalls = false
  }
}
const waitingTools = computed(() => {
  const results = new Set<string>()
  const calls: string[] = []
  for (const e of events.value) {
    if (e.type !== 'run.message') continue
    if (e.toolResult) results.add(e.toolResult.callId)
    for (const call of e.toolCalls) if (SCRIPT_WAITING_TOOLS.includes(call.name)) calls.push(call.id)
  }
  return calls.filter((id) => !results.has(id))
})
const scriptToolResults = computed(
  () =>
    events.value.filter(
      (e) => e.type === 'run.message' && e.toolResult && SCRIPT_WAITING_TOOLS.includes(e.toolResult.name),
    ).length,
)
watch(open, (value) => {
  if (value) refreshScriptCalls()
})
watch([active, scriptToolResults], ([value], [before]) => {
  // 実行中は記録を開いているので、Tool の結果が届くたびと、Run が終わったときに読み直す
  if (value || before) refreshScriptCalls()
})
const { pause, resume } = useIntervalFn(refreshScriptCalls, 5000, { immediate: false })
watch(
  () => active.value && waitingTools.value.length > 0,
  (waiting) => (waiting ? resume() : pause()),
  { immediate: true },
)
onMounted(() => {
  if (active.value) refreshScriptCalls()
})

// 最後の応答は、Run が終わって会話のメッセージになるまでここに出す
const pendingReply = computed(() => {
  if (!active.value) return ''
  const last = events.value.findLast((e) => e.type === 'run.message' && e.kind === RunEventKind.ASSISTANT_MESSAGE)
  return last?.type === 'run.message' && last.toolCalls.length === 0 ? last.text : ''
})
const streamingText = computed(() => props.delta || pendingReply.value)

const formattedResult = computed(() => (props.run.result == null ? '' : JSON.stringify(props.run.result, null, 2)))

// 出口へ送らないタスクの Run（修正チャット）で、送るはずだった通知（design.md §11.11）。終わった後に出す
const unsentNotice = computed(() => {
  const info = props.run.taskInfo
  if (!info || info.delivers || !info.destinationName || active.value) return null
  return { destination: info.destinationName, notification: info.notification }
})
</script>

<template>
  <div class="grid gap-3">
    <div class="flex flex-wrap items-center gap-2 text-sm">
      <RunStatusBadge :run="run" />
      <UBadge v-if="run.kind === RunKind.SKILL_EDIT" color="warning" variant="subtle" icon="i-lucide-pencil">
        {{ $t('Skillの編集') }}
      </UBadge>
      <UBadge v-if="run.kind === RunKind.TASK_EDIT" color="warning" variant="subtle" icon="i-lucide-pencil">
        {{ $t('タスクの編集') }}
      </UBadge>
      <UBadge v-if="run.taskInfo" color="info" variant="subtle" icon="i-lucide-workflow">
        {{ $t('タスク「{name}」・版 {number}', { name: run.taskInfo.name, number: run.taskInfo.number }) }}
      </UBadge>
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

    <!-- Skill の編集の Run で変えた Skill（design.md §11.6） -->
    <RunSkillChanges v-if="run.skillChanges.length" :changes="run.skillChanges" />
    <!-- タスクの編集の Run で保存したタスクの版（design.md §11.11） -->
    <RunTaskChanges v-if="run.taskChanges.length" :changes="run.taskChanges" />

    <RunTodoList v-if="run.todos.length" :todos="run.todos" />

    <template v-if="showLog">
      <template v-if="active">
        <RunEventLog :events="events" :script-calls="scriptCalls" active />
        <DBtn
          v-if="scriptCallsFailed"
          variant="link"
          color="warning"
          size="xs"
          class="px-0"
          icon="i-lucide-refresh-cw"
          @click="refreshScriptCalls"
        >
          {{ $t('スクリプトからの照会の記録を読めませんでした。読み直す') }}
        </DBtn>
      </template>
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
            <template v-else>
              <RunEventLog :events="events" :script-calls="scriptCalls" />
              <DBtn
                v-if="scriptCallsFailed"
                variant="link"
                color="warning"
                size="xs"
                class="px-0"
                icon="i-lucide-refresh-cw"
                @click="refreshScriptCalls"
              >
                {{ $t('スクリプトからの照会の記録を読めませんでした。読み直す') }}
              </DBtn>
            </template>
          </div>
        </template>
      </UCollapsible>
    </template>

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

    <div v-if="unsentNotice" class="grid gap-2 rounded-md border border-dashed border-default p-3 text-sm">
      <p class="flex items-center gap-2 text-muted">
        <UIcon name="i-lucide-send" class="size-4 shrink-0" />
        {{ $t('通知先「{name}」には送っていません（修正チャット）', { name: unsentNotice.destination }) }}
      </p>
      <template v-if="unsentNotice.notification">
        <p class="font-medium">{{ $t('送るはずだった通知の文章') }}</p>
        <p class="whitespace-pre-wrap">{{ unsentNotice.notification.message }}</p>
        <p v-if="unsentNotice.notification.files?.length" class="text-xs text-muted">
          {{ $t('渡すファイル：{files}', { files: unsentNotice.notification.files.join('、') }) }}
        </p>
      </template>
      <p v-else class="text-xs text-muted">
        {{ $t('通知の文章は作られていません（送るときは、決まった形の通知になります）') }}
      </p>
    </div>

    <template v-for="clarification in clarifications" :key="clarification.id">
      <!-- test_skill_draft の評価を待っている。利用者は答えず、評価が終わると作業が再開する（design.md §3.7.1） -->
      <UAlert
        v-if="clarification.kind === ClarificationKind.EVALUATION"
        color="primary"
        variant="subtle"
        icon="i-lucide-flask-conical"
        :title="
          $t('SkillのDraftを評価しています（{n}件）。終わると作業を再開します', { n: clarification.target?.cases ?? 0 })
        "
        :actions="
          clarification.target?.skill && clarification.target?.evaluation
            ? [
                {
                  label: $t('評価を見る'),
                  color: 'primary',
                  variant: 'outline',
                  to: `/skills/${clarification.target.skill}/evaluations/${clarification.target.evaluation}`,
                },
              ]
            : []
        "
      />
      <RunClarificationForm
        v-else
        :run-id="run.id"
        :clarification="clarification"
        @answered="emit('answered', $event)"
      />
    </template>
  </div>
</template>
