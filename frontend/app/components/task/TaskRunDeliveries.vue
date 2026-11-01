<script setup lang="ts">
/**
 * タスクの Run の配信（design.md §4.5・§11.9）。出口ごとの配信の状態、送った内容、送信の記録を出し、失敗・確認待ち・
 * 取消の配信は「通知を再試行」で送り直す（保存した内容を送る。エージェントは動かし直さない）。
 * 受付確認は送り先が受け取ったことだけを表し、Zapier 等での後の処理が終わったことは表さない。
 * 配信は Run の後に Worker が送るので、配信待ち・配信中・再試行待ちの間は間をおいて読み直す（配信の変化は画面へ
 * 知らせないため）
 */
import { AxiosError } from 'axios'

const props = defineProps<{ taskId: string; runId: string }>()
const emit = defineEmits<{ retried: [value: Delivery] }>()
const api = useAPI()
const toast = useToast()
const confirm = useConfirmDialog()
const { t } = useI18n()

// 1つのRunの配信は、通知のイベントごとに出口1つなので数件（1ページで読む）。読込みに世代を付け、後から始めた読込みや
// 送り直しの応答より前に始めた読込みの応答は使わない（送り直した配信を、古い一覧で失敗に戻さない）
const items = ref<Delivery[]>([])
const loading = ref(false)
let generation = 0
const fetchAll = async () => {
  const current = ++generation
  loading.value = true
  try {
    const page = await api.tasks(props.taskId).deliveries().list({ run: props.runId, pageSize: 100 })
    if (current === generation) items.value = page.results
  } finally {
    if (current === generation) loading.value = false
  }
}
const notifyLoadError = (err: unknown) =>
  toast.add({ title: t('配信を読めませんでした'), description: makeErrorMsg(err), color: 'error' })
defineExpose({ reload: fetchAll })
onMounted(() => fetchAll().catch(notifyLoadError))

// 配信待ち・配信中のものがあれば5秒ごとに、再試行待ちのものは次に送る日時を過ぎたら読み直す
const POLL_INTERVAL = 5000
const nextPollDelay = (): number | null => {
  const delays = items.value.flatMap((d) => {
    if (d.status === DeliveryStatus.PENDING || d.status === DeliveryStatus.SENDING) return [POLL_INTERVAL]
    if (d.status === DeliveryStatus.RETRY_WAIT && d.nextAttempt)
      return [Math.max(POLL_INTERVAL, new Date(d.nextAttempt).getTime() - Date.now() + POLL_INTERVAL)]
    return []
  })
  return delays.length ? Math.min(...delays) : null
}
let pollTimer: ReturnType<typeof setTimeout> | undefined
const schedulePoll = () => {
  clearTimeout(pollTimer)
  const delay = nextPollDelay()
  if (delay === null) return
  pollTimer = setTimeout(() => {
    // 読めなかったときは一覧が変わらないので、ここで次を予約する（読めたときは一覧の変化で予約し直す）
    fetchAll().catch(schedulePoll)
  }, delay)
}
watch(items, schedulePoll)
onBeforeUnmount(() => clearTimeout(pollTimer))

const eventLabels = computed(() => new Map(notifyEventItems(t).map((item) => [item.value, item.title])))
const kindIcon = (kind: DestinationKindEnum) => (kind === DestinationKind.IN_APP ? 'i-lucide-bell' : 'i-lucide-send')
const formatPayload = (payload: unknown) => JSON.stringify(payload, null, 2)

const retrying = ref<string | null>(null)
const retry = async (delivery: Delivery) => {
  const ok = await confirm({
    title: t('通知を再試行しますか？'),
    description: t('「{name}」へ、保存した内容をもう一度送ります（処理はやり直しません）', {
      name: delivery.destination.name,
    }),
    confirmLabel: t('再試行'),
  })
  if (!ok) return
  retrying.value = delivery.id
  let value: Delivery
  try {
    value = await api.tasks(props.taskId).deliveries(delivery.id).retry()
  } catch (err) {
    if (err instanceof AxiosError && !err.response) {
      // 送った後に応答だけが失われたときは、受け付けているかもしれない。読み直した状態で確かめてもらう
      toast.add({
        title: t('受け付けたかを確かめられませんでした'),
        description: t('配信の状態を読み直しました。配信待ち・配信中なら受け付けています'),
        color: 'warning',
      })
    } else {
      toast.add({ title: t('再試行できませんでした'), description: makeErrorMsg(err), color: 'error' })
    }
    await fetchAll().catch(notifyLoadError)
    return
  } finally {
    retrying.value = null
  }
  // 受け付けたことは、読み直しに失敗しても応答で示す（もう一度送らせない）。読込みの途中なら、その応答は捨てる
  generation++
  loading.value = false
  items.value = items.value.map((d) => (d.id === value.id ? value : d))
  toast.add({ title: t('送り直しを受け付けました'), color: 'success' })
  emit('retried', value)
  await fetchAll().catch(notifyLoadError)
}
</script>

<template>
  <div class="grid gap-3">
    <p v-if="!loading && items.length === 0" class="text-sm text-dimmed">{{ $t('配信はありません') }}</p>
    <UCard v-for="delivery in items" :key="delivery.id" :ui="{ body: 'grid gap-2 p-3 sm:p-3' }">
      <div class="flex flex-wrap items-center gap-2">
        <UIcon :name="kindIcon(delivery.destination.kind)" class="size-4 shrink-0 text-muted" />
        <span class="min-w-0 truncate text-sm font-medium">{{ delivery.destination.name }}</span>
        <span class="text-xs text-dimmed">
          {{
            $t('{event}の通知・版 {number}', {
              event: eventLabels.get(delivery.eventType) ?? delivery.eventType,
              number: delivery.destinationVersion,
            })
          }}
        </span>
        <UBadge :color="deliveryStatusColor(delivery.status)" variant="subtle" size="sm" class="ms-auto">
          {{ delivery.statusDisplay }}
        </UBadge>
      </div>

      <dl class="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-xs">
        <dt class="text-muted">{{ $t('送った回数') }}</dt>
        <dd>{{ delivery.attemptCount }}</dd>
        <template v-if="delivery.delivered">
          <dt class="text-muted">{{ $t('受付確認') }}</dt>
          <dd>{{ formatDatetime(delivery.delivered) }}</dd>
        </template>
        <template v-if="delivery.nextAttempt && delivery.status === DeliveryStatus.RETRY_WAIT">
          <dt class="text-muted">{{ $t('次に送る日時') }}</dt>
          <dd>{{ formatDatetime(delivery.nextAttempt) }}</dd>
        </template>
        <template v-if="delivery.lastError && delivery.status !== DeliveryStatus.DELIVERED">
          <dt class="text-muted">{{ $t('最後の誤り') }}</dt>
          <dd class="break-all whitespace-pre-wrap text-error">{{ delivery.lastError }}</dd>
        </template>
      </dl>
      <p v-if="delivery.status === DeliveryStatus.DELIVERED" class="text-xs text-dimmed">
        {{ $t('送り先が受け取ったことを表します。送り先での後の処理が終わったことは表しません') }}
      </p>
      <p v-else-if="delivery.status === DeliveryStatus.UNKNOWN" class="text-xs text-warning">
        {{ $t('届いたか分かりません。送り先で受け取ったかを確かめてから、必要なら再試行してください') }}
      </p>

      <div class="flex flex-wrap items-center gap-2">
        <UCollapsible>
          <DBtn variant="link" color="neutral" size="xs" class="px-0" trailing-icon="i-lucide-chevron-down">
            {{ $t('送った内容') }}
          </DBtn>
          <template #content>
            <pre class="mt-1 max-h-80 overflow-auto rounded-md bg-muted p-2 text-xs whitespace-pre-wrap">{{
              formatPayload(delivery.payload)
            }}</pre>
          </template>
        </UCollapsible>
        <UCollapsible v-if="delivery.attempts.length">
          <DBtn variant="link" color="neutral" size="xs" class="px-0" trailing-icon="i-lucide-chevron-down">
            {{ $t('送信の記録（{n}回）', { n: delivery.attempts.length }) }}
          </DBtn>
          <template #content>
            <ol class="mt-1 grid gap-1.5 text-xs">
              <li v-for="attempt in delivery.attempts" :key="attempt.number" class="rounded-md bg-muted p-2">
                <div class="flex flex-wrap gap-x-2">
                  <span class="font-medium">{{ $t('{n}回目', { n: attempt.number }) }}</span>
                  <span>{{ attempt.outcomeDisplay ?? $t('送信中') }}</span>
                  <span v-if="attempt.httpStatus" class="text-muted">HTTP {{ attempt.httpStatus }}</span>
                  <span class="text-dimmed">{{ formatDatetime(attempt.started) }}</span>
                </div>
                <p v-if="attempt.error" class="mt-0.5 break-all whitespace-pre-wrap text-error">{{ attempt.error }}</p>
                <ul v-if="attempt.fileFetches.length" class="mt-1 grid gap-0.5 text-muted">
                  <li v-for="(fetch, index) in attempt.fileFetches" :key="index">
                    {{
                      $t('{file}：{state}・{ip}・{time}', {
                        file: fetch.file,
                        state: fetch.served ? $t('取得') : $t('断った（{reason}）', { reason: fetch.refusal }),
                        ip: fetch.ipAddress ?? '—',
                        time: formatDatetime(fetch.created),
                      })
                    }}
                  </li>
                </ul>
              </li>
            </ol>
          </template>
        </UCollapsible>
        <DBtn
          v-if="isRetryableDelivery(delivery)"
          size="xs"
          color="primary"
          variant="outline"
          icon="i-lucide-rotate-cw"
          class="ms-auto"
          :loading="retrying === delivery.id"
          :disabled="retrying !== null"
          @click="retry(delivery)"
        >
          {{ $t('通知を再試行') }}
        </DBtn>
      </div>
    </UCard>
  </div>
</template>
