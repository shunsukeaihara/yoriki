<script setup lang="ts">
/**
 * タスクの Run の確認（ask_user）と回答（design.md §4.7「Webの画面」）。対話の出口（Slackのスレッド）のあるタスクの
 * Run は、この画面からも回答できる。答えられるのは、Run の作業の記録を見られる人と、手動実行・チャットから起動した
 * 本人で、確認を読めない人には API が 404・403 を返すので、そのときは何も出さない。
 *
 * 確認は確認ごとに1回だけ確定し、先に届いた回答を採る。Slackで先に答えられていたら（409）、知らせて読み直す。
 * 回答済みの確認は、誰がどこ（Web・Slack）で答えたかと回答を出す
 */
import { AxiosError } from 'axios'

const props = defineProps<{ runId: string }>()
const emit = defineEmits<{ answered: [] }>()
const api = useAPI()
const { t } = useI18n()

// 1つの Run の確認は数件なので、1ページで読む。読み直しが重なったら、後から始めたものだけを使う
const items = ref<Clarification[]>([])
const available = ref(true)
let generation = 0
const reload = async () => {
  const current = ++generation
  try {
    const page = await api.runs(props.runId).clarifications().list({ pageSize: 50 })
    if (current !== generation) return
    items.value = page.results
    available.value = true
  } catch (err) {
    // 確認を読めない人（結果だけを見る人等）には出さない
    if (err instanceof AxiosError && (err.response?.status === 404 || err.response?.status === 403)) {
      if (current === generation) available.value = false
      return
    }
    throw err
  }
}
defineExpose({ reload })
// 最初の読込みは裏で行う（読めなくても、次の読み直しで戻る）
onMounted(() => reload().catch(() => {}))

// 答える確認（評価の完了待ちは利用者が答えないので除く）と、回答済みの確認（古い順）
const open = computed(() =>
  items.value.filter((c) => c.status === ClarificationStatus.OPEN && c.kind !== ClarificationKind.EVALUATION),
)
const answered = computed(() =>
  items.value
    .filter((c) => c.status === ClarificationStatus.ANSWERED)
    .sort((a, b) => (a.answered ?? a.created).localeCompare(b.answered ?? b.created)),
)

/** どこで答えたか（Web、または対話の出口の種類） */
const viaLabel = (via: string) => {
  switch (via) {
    case 'web':
      return t('Web')
    case 'slack':
      return t('Slack')
    default:
      return via
  }
}
const answeredLabel = (c: Clarification) => {
  const via = viaLabel(c.answeredVia)
  if (c.answeredByName && via) return t('{name}さんが{via}で回答しました', { name: c.answeredByName, via })
  if (via) return t('{via}で回答しました', { via })
  if (c.answeredByName) return t('{name}さんが回答しました', { name: c.answeredByName })
  return t('回答しました')
}
const answerText = (c: Clarification, index: number) => {
  const answer = c.answers[index]
  if (!answer) return '—'
  const parts = [...(answer.selected ?? []), ...(answer.text?.trim() ? [answer.text.trim()] : [])]
  return parts.length ? parts.join('、') : '—'
}

const onAnswered = async () => {
  emit('answered')
  await reload().catch(() => {})
}
</script>

<template>
  <section
    v-if="available && (open.length || answered.length)"
    class="grid gap-3"
    data-testid="task-run-clarifications"
  >
    <h3 class="text-xs font-semibold text-muted">{{ $t('質問と回答') }}</h3>
    <div v-if="open.length" class="grid gap-3">
      <UAlert
        color="warning"
        variant="subtle"
        icon="i-lucide-message-circle-question"
        :title="$t('エージェントが回答を待っています')"
        :description="$t('ここで答えると、作業を再開します。対話の出口のSlackのスレッドからも答えられます')"
      />
      <RunClarificationForm
        v-for="clarification in open"
        :key="clarification.id"
        :run-id="runId"
        :clarification="clarification"
        @answered="onAnswered"
        @conflicted="onAnswered"
      />
    </div>
    <UCard v-for="clarification in answered" :key="clarification.id" :ui="{ body: 'grid gap-2 p-3 sm:p-3' }">
      <dl class="grid gap-2 text-sm">
        <div v-for="(question, index) in clarification.questions" :key="index" class="grid gap-0.5">
          <dt class="flex flex-wrap items-center gap-2">
            <UBadge v-if="question.header" color="neutral" variant="subtle" size="sm">{{ question.header }}</UBadge>
            <span class="whitespace-pre-wrap text-muted">{{ question.question }}</span>
          </dt>
          <dd class="whitespace-pre-wrap">{{ answerText(clarification, index) }}</dd>
        </div>
      </dl>
      <p v-if="clarification.comment" class="text-sm whitespace-pre-wrap">
        {{ $t('補足：{comment}', { comment: clarification.comment }) }}
      </p>
      <p class="text-xs text-dimmed">
        {{ answeredLabel(clarification)
        }}<template v-if="clarification.answered">・{{ formatDatetime(clarification.answered) }}</template>
      </p>
    </UCard>
  </section>
</template>
