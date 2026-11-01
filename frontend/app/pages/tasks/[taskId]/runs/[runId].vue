<script setup lang="ts">
/**
 * タスクの Run（design.md §11.9「自動実行の履歴」）。起動のしかたと起動元、受付時の版と入力、処理の状態、作業の記録と
 * 応答（会話の表示を読むだけで使う）、業務結果、添付と成果物、通知の配信の状態を出す。見られるのは Run の結果とログを
 * 見られる人（タスクを管理できる人。authorization.md §7.1）。「処理完了・通知失敗」でも結果は残り、「通知を再試行」は
 * 保存した内容を送り直すだけで、処理はやり直さない（処理の再実行はまだない）
 */
import TaskRunDeliveries from '~/components/task/TaskRunDeliveries.vue'

const route = useRoute()
const api = useAPI()
const toast = useToast()
const confirm = useConfirmDialog()
const { t } = useI18n()
const taskId = route.params.taskId as string
const runId = route.params.runId as string

// タスクの定義は読まない（タスクを移した・消した後も、Runの結果を見られる人は開ける。authorization.md §7.1）
const {
  data: run,
  error: runError,
  refresh: refreshRun,
} = await useAsyncData(`task-${taskId}-run-${runId}`, () => api.tasks(taskId).runs(runId).get())
if (runError.value) throw createError(runError.value)

const messages = useMessages((params) => api.tasks(taskId).runs(runId).messages().list(params))
const {
  items: files,
  loading: filesLoading,
  fetchAll: fetchFiles,
} = useFetchAll(() => api.runs(runId).artifacts().list({ pageSize: 200 }))
await Promise.all([messages.refresh(), fetchFiles()])

const deliveriesRef = ref<InstanceType<typeof TaskRunDeliveries> | null>(null)

/** 読み直す。イベントが続けて届いても、まとめて1回にする（会話の画面と同じ） */
let syncing = false
let syncAgain = false
const syncNow = async () => {
  if (syncing) {
    syncAgain = true
    return
  }
  syncing = true
  try {
    do {
      syncAgain = false
      await Promise.all([refreshRun(), messages.refresh(), fetchFiles(), deliveriesRef.value?.reload()])
    } while (syncAgain)
  } catch {
    // 裏の更新なので、失敗しても次のイベントか「読み直す」で戻る
  } finally {
    syncing = false
  }
}
const sync = useDebounceFn(syncNow, 300, { maxWait: 2000 })
const reload = async () => {
  await syncNow()
}

// 動いている間は Run のストリームを受け取る（終わったら、最後のイベントで読み直す）
const activeRunId = computed(() => (run.value && isActiveRun(run.value) ? run.value.id : null))
const live = useRunLive(activeRunId, { onEvent: () => sync(), onReconnect: () => sync() })

// --- 表示 ---

const listPath = computed(() => `/tasks/${taskId}`)
const timeline = computed(() => buildTimeline(messages.items.value))
const timelineById = computed(() => new Map(timeline.value.map((item) => [item.id, item])))
const chatMessages = computed(() =>
  timeline.value.map((item) => ({
    id: item.id,
    role: item.kind === 'message' && item.message.role === MessageRole.USER ? 'user' : 'assistant',
    parts: [{ type: 'text', text: item.kind === 'message' ? item.message.body : '' }],
  })),
)
const itemOf = (id: string) => timelineById.value.get(id)
const chatStatus = computed(() => (activeRunId.value ? 'streaming' : 'ready'))

const formatValue = (value: unknown) =>
  value === null || value === undefined || value === ''
    ? '—'
    : typeof value === 'string'
      ? value
      : JSON.stringify(value)

const launchedBy = computed(() => {
  const value = run.value
  if (!value) return ''
  if (value.startedBy) return value.startedBy.displayName || value.startedBy.email
  if (value.receipt)
    return t('入口「{name}」（版 {number}）', {
      name: value.receipt.bindingName,
      number: value.receipt.bindingVersion,
    })
  return ''
})

// --- 操作 ---

const stopping = ref(false)
const stop = async () => {
  const ok = await confirm({
    title: t('このRunを停止しますか？'),
    description: t('作業は次の区切りで止まります。停止の通知を設定していれば、通知も送ります'),
    confirmLabel: t('停止'),
    color: 'error',
  })
  if (!ok) return
  stopping.value = true
  try {
    await api.runs(runId).cancel({})
    await syncNow()
  } catch (err) {
    toast.add({ title: t('停止できませんでした'), description: makeErrorMsg(err), color: 'error' })
  } finally {
    stopping.value = false
  }
}
</script>

<template>
  <UDashboardPanel :id="`task-run-${runId}`">
    <template #header>
      <UDashboardNavbar :title="run?.taskName">
        <template #leading>
          <DBtn
            :to="listPath"
            color="neutral"
            variant="ghost"
            icon="i-lucide-arrow-left"
            :aria-label="$t('タスクへ')"
          />
        </template>
        <template #right>
          <DBtn color="neutral" variant="ghost" icon="i-lucide-refresh-cw" :aria-label="$t('読み直す')" @click="reload">
            <span class="hidden sm:inline">{{ $t('読み直す') }}</span>
          </DBtn>
          <DBtn
            v-if="run && isCancellable(run)"
            color="error"
            variant="ghost"
            icon="i-lucide-square"
            :loading="stopping"
            :aria-label="$t('停止')"
            @click="stop"
          >
            <span class="hidden sm:inline">{{ $t('停止') }}</span>
          </DBtn>
        </template>
      </UDashboardNavbar>
    </template>

    <template #body>
      <div v-if="run" class="mx-auto grid w-full max-w-6xl min-w-0 gap-6 lg:grid-cols-[minmax(0,1fr)_20rem]">
        <!-- 狭い画面では概要・通知・ファイルを先に、広い画面では右の欄に出す -->
        <section class="order-2 grid min-w-0 content-start gap-4 lg:order-1">
          <div v-if="messages.hasOlder.value" class="flex justify-center">
            <DBtn variant="ghost" color="neutral" :loading="messages.loadingOlder.value" @click="messages.loadOlder()">
              {{ $t('以前のメッセージを表示') }}
            </DBtn>
          </div>
          <UChatMessages :messages="chatMessages" :status="chatStatus" :should-scroll-to-bottom="false">
            <template #content="{ message }">
              <template v-for="item in [itemOf(message.id)]" :key="item?.id">
                <template v-if="item?.kind === 'run'">
                  <RunWork
                    v-if="item.runId === run.id"
                    :run="run"
                    :events="activeRunId ? live.events.value : undefined"
                    :delta="activeRunId ? live.delta.value : undefined"
                  />
                </template>
                <template v-else-if="item?.kind === 'message'">
                  <MarkdownContent v-if="item.message.role === MessageRole.AGENT" :text="item.message.body" />
                  <p
                    v-else-if="item.message.role === MessageRole.SYSTEM"
                    class="flex items-start gap-2 text-sm whitespace-pre-wrap text-muted"
                  >
                    <UIcon name="i-lucide-info" class="mt-0.5 size-4 shrink-0" />
                    <span>{{ item.message.body }}</span>
                  </p>
                  <!-- 最初の依頼（タスクの依頼文に入力の値を埋め込んだもの。埋め込んだ値は目立たせる） -->
                  <p v-else><TaskPromptText :text="item.message.body" /></p>
                  <ul v-if="item.message.attachmentsInfo.length" class="mt-1 grid gap-0.5">
                    <li v-for="artifact in item.message.attachmentsInfo" :key="artifact.id">
                      <ArtifactLink :run-id="runId" :artifact="artifact" />
                    </li>
                  </ul>
                </template>
              </template>
            </template>
          </UChatMessages>
        </section>

        <aside class="order-1 grid min-w-0 content-start gap-6 lg:order-2">
          <section class="grid gap-2">
            <h3 class="text-xs font-semibold text-muted">{{ $t('概要') }}</h3>
            <div class="flex flex-wrap items-center gap-1.5">
              <RunStatusBadge :run="run" />
              <span
                v-if="run.endReasonDisplay && run.endReasonDisplay !== run.statusDisplay"
                class="text-xs text-muted"
                >{{ run.endReasonDisplay }}</span
              >
            </div>
            <dl class="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-sm">
              <dt class="text-muted">{{ $t('起動') }}</dt>
              <dd class="min-w-0 break-words">
                {{ run.launchDisplay }}<template v-if="launchedBy"><br />{{ launchedBy }}</template>
              </dd>
              <template v-if="run.receipt?.dedupKey">
                <dt class="text-muted">{{ $t('重複のキー') }}</dt>
                <dd class="min-w-0 font-mono text-xs break-all">{{ run.receipt.dedupKey }}</dd>
              </template>
              <dt class="text-muted">{{ $t('タスクの版') }}</dt>
              <dd>{{ $t('版 {number}', { number: run.taskVersion }) }}</dd>
              <dt class="text-muted">{{ $t('モデル') }}</dt>
              <dd class="min-w-0 break-words">
                {{ run.modelProfileName }}<template v-if="run.effort"> · {{ run.effort }}</template>
              </dd>
              <dt class="text-muted">{{ $t('受付') }}</dt>
              <dd>{{ formatDatetime(run.created) }}</dd>
              <dt class="text-muted">{{ $t('終了') }}</dt>
              <dd>{{ run.finished ? formatDatetime(run.finished) : '—' }}</dd>
            </dl>
          </section>

          <section v-if="run.inputs.length" class="grid gap-2">
            <h3 class="text-xs font-semibold text-muted">{{ $t('入力') }}</h3>
            <dl class="grid gap-1.5 text-sm">
              <div v-for="input in run.inputs" :key="input.name">
                <dt class="text-xs text-muted">{{ input.label }}</dt>
                <dd class="break-words whitespace-pre-wrap">{{ formatValue(input.value) }}</dd>
              </div>
            </dl>
          </section>

          <section class="grid gap-2">
            <h3 class="text-xs font-semibold text-muted">{{ $t('通知') }}</h3>
            <TaskRunDeliveries ref="deliveriesRef" :task-id="taskId" :run-id="runId" />
          </section>

          <section class="grid gap-2">
            <h3 class="text-xs font-semibold text-muted">{{ $t('添付と成果物') }}</h3>
            <ConversationFiles :run-id="runId" :items="files" :loading="filesLoading" />
          </section>
        </aside>
      </div>
    </template>
  </UDashboardPanel>
</template>
