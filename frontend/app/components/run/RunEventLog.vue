<script setup lang="ts">
/**
 * Run の作業の記録（Tool の呼出しと結果、途中の説明、モデルの切り替え等）。
 * 利用者の最初の依頼とエージェントの最後の応答は会話に出るので、ここには出さない。
 * Skill のスクリプトからの Connector の呼出し（scriptCalls）は、そのときに実行していた Tool の呼出し（shell・
 * job_output）の下に出す。Tool の呼出しの間（ステップの区切り）に処理したものは、時刻の順で間に出す。
 * サブエージェントの記録（events・scriptCalls の subagent が空でないもの）は、起動した subagent の呼出しの下に
 * 入れ子で出す（harness.md §10.11）。子の記録を出すとき（subagent を渡す）は、子の最後の応答（親への報告）も出す。
 */
const props = withDefaults(
  defineProps<{
    events: RunStreamEvent[]
    scriptCalls?: ScriptCall[]
    /** Run のサブエージェントの状態（親の記録を出すときに渡す） */
    subagents?: Subagent[]
    active?: boolean
    /** 子の記録を出すときの、その子 */
    subagent?: Subagent
  }>(),
  { scriptCalls: () => [], subagents: () => [], active: false, subagent: undefined },
)
const { t } = useI18n()

// サーバーの日時（ISO 8601）をマイクロ秒にする。Date.parse はミリ秒までしか見ず、同じミリ秒の前後を比べられない
const microseconds = (value: string) => {
  const fraction = value.match(/\.(\d+)/)?.[1] ?? ''
  return Date.parse(value.replace(/\.\d+/, '')) * 1000 + Number(fraction.padEnd(6, '0').slice(0, 6))
}

type LogEntry =
  | { kind: 'text'; key: string; text: string }
  | {
      kind: 'tool'
      key: string
      call: ToolCallInfo
      result: ToolResultInfo | null
      scripts: ScriptCall[]
      subagent: Subagent | null
    }
  | { kind: 'scripts'; key: string; scripts: ScriptCall[] }
  | { kind: 'note'; key: string; icon: string; text: string; count: number }

// 子の記録を出すときは、親が渡した分（その子の記録）がすべて自分のもの
const isOwn = (item: { subagent: string }) => !!props.subagent || !item.subagent
const own = computed(() => props.events.filter((e) => e.type !== 'run.delta' && isOwn(e)))
const ownScripts = computed(() => props.scriptCalls.filter(isOwn))

// 子ごとの記録と照会（親の記録を出すときだけ）
const byChild = <T extends { subagent: string }>(items: T[]) => {
  const map = new Map<string, T[]>()
  if (props.subagent) return map
  for (const item of items) if (item.subagent) map.set(item.subagent, [...(map.get(item.subagent) ?? []), item])
  return map
}
const childEvents = computed(() =>
  byChild(props.events.filter((e): e is Exclude<RunStreamEvent, { type: 'run.delta' }> => e.type !== 'run.delta')),
)
const childScripts = computed(() => byChild(props.scriptCalls))
const subagentByCall = computed(() => new Map(props.subagents.map((s) => [s.toolCallId, s])))

const entries = computed<LogEntry[]>(() => {
  const results = new Map<string, ToolResultInfo>()
  const callIds = new Set<string>()
  for (const e of own.value) {
    if (e.type === 'run.message' && e.toolResult) results.set(e.toolResult.callId, e.toolResult)
    if (e.type === 'run.message') for (const call of e.toolCalls) callIds.add(call.id)
  }
  const during = new Map<string, ScriptCall[]>()
  const between: ScriptCall[] = []
  for (const call of ownScripts.value) {
    if (call.during && callIds.has(call.during)) during.set(call.during, [...(during.get(call.during) ?? []), call])
    else between.push(call)
  }
  const list: LogEntry[] = []
  // ステップの区切りで処理した呼出しを、その時刻より後の記録の前に出す（API の一覧は時刻の順）
  let next = 0
  const flushBetween = (before?: string) => {
    const start = next
    const limit = before === undefined ? Infinity : microseconds(before)
    while (next < between.length && microseconds(between[next]!.created) < limit) next += 1
    if (next > start) list.push({ kind: 'scripts', key: `scripts-${start}`, scripts: between.slice(start, next) })
  }
  // 同じ記録が続いたら（再試行等）、1行にまとめて回数を出す
  const note = (key: string, icon: string, text: string) => {
    const last = list.at(-1)
    if (last?.kind === 'note' && last.text === text) last.count += 1
    else list.push({ kind: 'note', key, icon, text, count: 1 })
  }
  let requests = 0
  for (const e of own.value) {
    if (e.type === 'run.delta') continue
    flushBetween(e.created)
    const key = String(e.sequence)
    switch (e.type) {
      case 'run.message':
        if (e.kind === RunEventKind.USER_MESSAGE) {
          // 2つ目からは、実行中に取り込んだ追加の指示
          if (requests++ > 0) note(key, 'i-lucide-message-square-plus', t('追加の指示を取り込みました'))
        } else if (e.kind === RunEventKind.ASSISTANT_MESSAGE && (e.toolCalls.length > 0 || props.subagent)) {
          if (e.text.trim()) list.push({ kind: 'text', key, text: e.text })
          for (const call of e.toolCalls)
            list.push({
              kind: 'tool',
              key: `${key}-${call.id}`,
              call,
              result: results.get(call.id) ?? null,
              scripts: during.get(call.id) ?? [],
              subagent: call.name === 'subagent' ? (subagentByCall.value.get(call.id) ?? null) : null,
            })
        }
        break
      case 'run.event':
        if (!NOTABLE_EVENT_KINDS.includes(e.kind)) break
        note(
          key,
          'i-lucide-info',
          e.comment ? t('{kind}：{comment}', { kind: e.kindDisplay, comment: e.comment }) : e.kindDisplay,
        )
        break
      case 'run.status':
        if (e.comment) note(key, 'i-lucide-activity', e.comment)
        break
    }
  }
  flushBetween()
  return list
})

const formatArguments = (call: ToolCallInfo) => JSON.stringify(call.arguments, null, 2)
</script>

<template>
  <div class="grid gap-2">
    <template v-for="entry in entries" :key="entry.key">
      <MarkdownContent v-if="entry.kind === 'text'" :text="entry.text" class="text-sm text-muted" />
      <div v-else-if="entry.kind === 'tool'" class="grid gap-1">
        <UChatTool
          :text="toolLabel(entry.call, t)"
          :icon="entry.result?.isError ? 'i-lucide-triangle-alert' : 'i-lucide-wrench'"
          :suffix="entry.result?.isError ? $t('エラー') : undefined"
          :loading="active && !entry.result"
          :streaming="active && !entry.result"
        >
          <div class="grid gap-2 text-xs">
            <pre class="overflow-x-auto rounded-md bg-muted p-2 whitespace-pre-wrap">{{
              formatArguments(entry.call)
            }}</pre>
            <pre v-if="entry.result?.text" class="max-h-80 overflow-auto rounded-md bg-muted p-2 whitespace-pre-wrap">{{
              entry.result.text
            }}</pre>
          </div>
        </UChatTool>
        <RunScriptCalls v-if="entry.scripts.length" :calls="entry.scripts" during class="ml-6" />
        <RunSubagent
          v-if="entry.subagent"
          :subagent="entry.subagent"
          :events="childEvents.get(entry.subagent.id) ?? []"
          :script-calls="childScripts.get(entry.subagent.id) ?? []"
          :active="active"
          class="ml-6"
        />
      </div>
      <RunScriptCalls v-else-if="entry.kind === 'scripts'" :calls="entry.scripts" />
      <p v-else class="flex items-start gap-2 text-sm text-muted">
        <UIcon :name="entry.icon" class="mt-1 size-4 shrink-0" />
        <span class="whitespace-pre-wrap">
          {{ entry.count > 1 ? $t('{text}（{count}回）', { text: entry.text, count: entry.count }) : entry.text }}
        </span>
      </p>
    </template>
  </div>
</template>
