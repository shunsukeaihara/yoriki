<script setup lang="ts">
/**
 * Run の作業の記録（Tool の呼出しと結果、途中の説明、モデルの切り替え等）。
 * 利用者の最初の依頼とエージェントの最後の応答は会話に出るので、ここには出さない。
 */
const props = defineProps<{ events: RunStreamEvent[]; active?: boolean }>()
const { t } = useI18n()

type LogEntry =
  | { kind: 'text'; key: string; text: string }
  | { kind: 'tool'; key: string; call: ToolCallInfo; result: ToolResultInfo | null }
  | { kind: 'note'; key: string; icon: string; text: string; count: number }

const entries = computed<LogEntry[]>(() => {
  const results = new Map<string, ToolResultInfo>()
  for (const e of props.events) {
    if (e.type === 'run.message' && e.toolResult) results.set(e.toolResult.callId, e.toolResult)
  }
  const list: LogEntry[] = []
  // 同じ記録が続いたら（再試行等）、1行にまとめて回数を出す
  const note = (key: string, icon: string, text: string) => {
    const last = list.at(-1)
    if (last?.kind === 'note' && last.text === text) last.count += 1
    else list.push({ kind: 'note', key, icon, text, count: 1 })
  }
  let requests = 0
  for (const e of props.events) {
    const key = String(e.type === 'run.delta' ? '' : e.sequence)
    switch (e.type) {
      case 'run.message':
        if (e.kind === RunEventKind.USER_MESSAGE) {
          // 2つ目からは、実行中に取り込んだ追加の指示
          if (requests++ > 0) note(key, 'i-lucide-message-square-plus', t('追加の指示を取り込みました'))
        } else if (e.kind === RunEventKind.ASSISTANT_MESSAGE && e.toolCalls.length > 0) {
          if (e.text.trim()) list.push({ kind: 'text', key, text: e.text })
          for (const call of e.toolCalls)
            list.push({ kind: 'tool', key: `${key}-${call.id}`, call, result: results.get(call.id) ?? null })
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
  return list
})

const formatArguments = (call: ToolCallInfo) => JSON.stringify(call.arguments, null, 2)
</script>

<template>
  <div class="grid gap-2">
    <template v-for="entry in entries" :key="entry.key">
      <MarkdownContent v-if="entry.kind === 'text'" :text="entry.text" class="text-sm text-muted" />
      <UChatTool
        v-else-if="entry.kind === 'tool'"
        :text="toolLabel(entry.call.name, t)"
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
      <p v-else class="flex items-start gap-2 text-sm text-muted">
        <UIcon :name="entry.icon" class="mt-1 size-4 shrink-0" />
        <span class="whitespace-pre-wrap">
          {{ entry.count > 1 ? $t('{text}（{count}回）', { text: entry.text, count: entry.count }) : entry.text }}
        </span>
      </p>
    </template>
  </div>
</template>
