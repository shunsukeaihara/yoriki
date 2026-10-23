<script setup lang="ts">
/**
 * 会話の画面（design.md §11）。表示は REST API で取得し、実行中の Run のストリームと、ログインユーザー宛ての通知を
 * 受けたら取得し直す（realtime.md §5）。画面を閉じても Run は続き、開き直せば同じ表示に戻る。
 */
const route = useRoute()
const api = useAPI()
const toast = useToast()
const confirm = useConfirmDialog()
const { t } = useI18n()
const listStore = useConversationListStore()
const conversationId = route.params.conversationId as string

const {
  data: conversation,
  error,
  refresh: refreshConversation,
} = await useAsyncData(`conversation-${conversationId}`, () => api.conversations(conversationId).get())
if (error.value) throw createError(error.value)

const { data: models } = await useAsyncData('app-model-profiles', () => api.modelProfiles.list(), {
  default: () => [],
})

const messages = useConversationMessages(conversationId)
const { items: runs, fetchAll: fetchRuns } = useFetchAll(() =>
  api.runs().list({ conversation: conversationId, pageSize: 200 }),
)
// 添付と成果物（送った添付と、Run が作った成果物）
const {
  items: files,
  loading: filesLoading,
  fetchAll: fetchFiles,
} = useFetchAll(() => api.conversations(conversationId).artifacts().list({ pageSize: 200 }))
await Promise.all([messages.refresh(), fetchRuns(), fetchFiles()])

const activeRun = computed(() => conversation.value?.activeRun ?? null)
const activeRunId = computed(() => activeRun.value?.id ?? null)

// 回答待ちの確認（実行中の Run のもの。件数は少ないので1ページで読む）。読み直しが重なったり、読む間に Run が
// 替わったりしたら、その応答は使わない（前の Run の確認を、新しい Run の作業に出さないように）
const clarifications = ref<Clarification[]>([])
let clarificationRequest = 0
const fetchClarifications = async () => {
  const request = ++clarificationRequest
  const runId = activeRunId.value
  const results = runId
    ? (await api.runs(runId).clarifications().list({ status: ClarificationStatus.OPEN, pageSize: 50 })).results
    : []
  if (request === clarificationRequest && runId === activeRunId.value) clarifications.value = results
}
await fetchClarifications()
const hasOpenQuestion = computed(() => clarifications.value.some((c) => c.kind === ClarificationKind.QUESTION))

/**
 * 会話・メッセージ・Run・確認を取得し直す。イベントが続けて届いても、まとめて1回にする。
 * 取得の途中で次の取得を頼まれたら、終わってからもう一度取得する（古い応答で新しい表示を上書きしないように）
 */
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
      await Promise.all([refreshConversation(), messages.refresh(), fetchRuns(), fetchFiles()])
      // 確認は、読み直した会話の実行中の Run のものを読む
      await fetchClarifications()
      if (conversation.value) listStore.upsert(conversation.value)
    } while (syncAgain)
  } catch {
    // 裏の更新なので、失敗しても次のイベントか再表示で戻る
  } finally {
    syncing = false
  }
}
const sync = useDebounceFn(syncNow, 300, { maxWait: 2000 })

// 接続し直したときは、切れていた間の変化（保存しない通知、LLM の出力の差分）を取得し直す
const live = useRunLive(activeRunId, { onEvent: () => sync(), onReconnect: () => sync() })
// 別のタブや外部のスレッドから、この会話で新しい Run が始まった場合
useUserEvents(
  (event) => {
    if (event.conversation === conversationId && event.run !== activeRunId.value) sync()
  },
  { onReconnect: () => sync() },
)

// --- 表示 ---

const runById = computed(() => {
  const map = new Map(runs.value.map((r) => [r.id, r]))
  if (activeRun.value) map.set(activeRun.value.id, activeRun.value)
  return map
})
// 作業の表示は、出すものがある Run だけにする
const timeline = computed(() =>
  buildTimeline(messages.items.value).filter((item) => {
    if (item.kind !== 'run') return true
    const run = runById.value.get(item.runId)
    return run !== undefined && hasWorkToShow(run)
  }),
)
const timelineById = computed(() => new Map(timeline.value.map((item) => [item.id, item])))
// UChatMessages に渡す形（本文は #content で描く）
const chatMessages = computed(() =>
  timeline.value.map((item) => ({
    id: item.id,
    role: item.kind === 'message' && item.message.role === MessageRole.USER ? 'user' : 'assistant',
    parts: [{ type: 'text', text: item.kind === 'message' ? item.message.body : '' }],
  })),
)
const itemOf = (id: string) => timelineById.value.get(id)

const chatStatus = computed(() => {
  if (sending.value) return 'submitted'
  return activeRun.value ? 'streaming' : 'ready'
})

const instructionLabel = (message: Message) => {
  const instruction = message.instruction
  if (!instruction) return ''
  const kind = instruction.kind === InstructionKind.NEXT ? t('次の依頼') : t('追加の指示')
  return t('{kind}・{state}', { kind, state: instruction.stateDisplay })
}

// --- 操作 ---

const text = ref('')
const mode = ref<string>(MessageMode.AUTO)
const sending = ref(false)
// 送信に失敗して送り直すときは同じ ID を使い、サーバーで二重に受け付けないようにする（design.md §5.5）
let clientMessageId = crypto.randomUUID()

// 添付は選んだらすぐにアップロードし、送るときに ID を付ける
const attachments = useAttachmentUploads()
const attach = (selected: File[]) => {
  attachments.add(selected)
  attachments.upload(conversationId)
}

const send = async () => {
  const body = text.value
  sending.value = true
  try {
    const message = await api
      .conversations(conversationId)
      .messages()
      .create({
        body,
        attachments: attachments.ids.value,
        clientMessageId,
        mode: mode.value as MessageWrite['mode'],
      })
    clientMessageId = crypto.randomUUID()
    text.value = ''
    mode.value = MessageMode.AUTO
    attachments.clear()
    messages.add(message)
    sync()
  } catch (err) {
    toast.add({ title: t('送信できませんでした'), description: makeErrorMsg(err), color: 'error' })
  } finally {
    sending.value = false
  }
}

const stopping = ref(false)
const stop = async () => {
  if (!activeRun.value) return
  stopping.value = true
  try {
    await api.runs(activeRun.value.id).cancel({})
    sync()
  } catch (err) {
    toast.add({ title: t('停止できませんでした'), description: makeErrorMsg(err), color: 'error' })
  } finally {
    stopping.value = false
  }
}

const onUpdated = (value: Conversation) => {
  conversation.value = value
  listStore.upsert(value)
}

const onDelete = async () => {
  const ok = await confirm({
    title: t('会話を削除しますか？'),
    description: activeRun.value ? t('実行中の作業は停止します。') : undefined,
    confirmLabel: t('削除'),
    color: 'error',
  })
  if (!ok) return
  try {
    await api.conversations(conversationId).delete()
    listStore.remove(conversationId)
    toast.add({ title: t('削除しました'), color: 'success' })
    await navigateTo('/conversations')
  } catch (err) {
    toast.add({ title: t('エラー'), description: makeErrorMsg(err), color: 'error' })
  }
}

// 添付と成果物の欄。広い画面では右の欄に、狭い画面ではドロワーに出す
// （ページの根は1つにするので、会話の欄とこの欄を display: contents の div で包み、ダッシュボードの横並びにする）
const isWide = useMediaQuery('(min-width: 1024px)')
const filesPanelOpen = ref(true)
const filesDrawerOpen = ref(false)
const toggleFiles = () => {
  if (isWide.value) filesPanelOpen.value = !filesPanelOpen.value
  else filesDrawerOpen.value = true
}

const menuItems = computed(() => [
  [{ label: t('会話を削除'), icon: 'i-lucide-trash-2', color: 'error' as const, onSelect: onDelete }],
])
</script>

<template>
  <div class="contents">
    <UDashboardPanel :id="`conversation-${conversationId}`" :ui="{ body: 'p-0 sm:p-0' }">
      <template #header>
        <UDashboardNavbar :title="conversation?.title || $t('新しい会話')">
          <template #right>
            <ConversationModelSettings
              v-if="conversation"
              :conversation="conversation"
              :models="models"
              class="hidden sm:flex"
              @updated="onUpdated"
            />
            <DBtn
              icon="i-lucide-folder-open"
              variant="ghost"
              color="neutral"
              :aria-label="$t('添付と成果物')"
              @click="toggleFiles"
            />
            <UDropdownMenu :items="menuItems">
              <DBtn icon="i-lucide-ellipsis" variant="ghost" color="neutral" :aria-label="$t('メニュー')" />
            </UDropdownMenu>
          </template>
        </UDashboardNavbar>
        <!-- 狭い画面では、モデルの切り替えを見出しの下の行に出す -->
        <UDashboardToolbar v-if="conversation" class="sm:hidden">
          <ConversationModelSettings :conversation="conversation" :models="models" @updated="onUpdated" />
        </UDashboardToolbar>
      </template>

      <template #body>
        <UContainer class="flex max-w-3xl flex-1 flex-col gap-4 pt-4 sm:gap-6">
          <div v-if="messages.hasOlder.value" class="flex justify-center">
            <DBtn variant="ghost" color="neutral" :loading="messages.loadingOlder.value" @click="messages.loadOlder()">
              {{ $t('以前のメッセージを表示') }}
            </DBtn>
          </div>

          <UChatMessages
            :messages="chatMessages"
            :status="chatStatus"
            should-auto-scroll
            :spacing-offset="160"
            class="flex-1"
          >
            <template #content="{ message }">
              <template v-for="item in [itemOf(message.id)]" :key="item?.id">
                <template v-if="item?.kind === 'run' && runById.has(item.runId)">
                  <RunWork
                    :run="runById.get(item.runId)!"
                    :events="item.runId === activeRunId ? live.events.value : undefined"
                    :delta="item.runId === activeRunId ? live.delta.value : undefined"
                    :clarifications="item.runId === activeRunId ? clarifications : undefined"
                    @answered="sync()"
                  />
                </template>
                <template v-else-if="item?.kind === 'message'">
                  <MarkdownContent v-if="item.message.role === MessageRole.AGENT" :text="item.message.body" />
                  <!-- システムのメッセージ（次の依頼を始められなかった理由等） -->
                  <p
                    v-else-if="item.message.role === MessageRole.SYSTEM"
                    class="flex items-start gap-2 text-sm whitespace-pre-wrap text-muted"
                  >
                    <UIcon name="i-lucide-info" class="mt-0.5 size-4 shrink-0" />
                    <span>{{ item.message.body }}</span>
                  </p>
                  <p v-else-if="item.message.body" class="whitespace-pre-wrap">{{ item.message.body }}</p>
                  <ul v-if="item.message.attachmentsInfo.length" class="mt-1 grid gap-0.5">
                    <li v-for="artifact in item.message.attachmentsInfo" :key="artifact.id">
                      <ArtifactLink :conversation-id="conversationId" :artifact="artifact" />
                    </li>
                  </ul>
                  <UBadge
                    v-if="item.message.instruction"
                    :color="isWaitingNextRequest(item.message) ? 'warning' : 'neutral'"
                    variant="outline"
                    size="sm"
                    class="mt-2"
                  >
                    {{ instructionLabel(item.message) }}
                  </UBadge>
                </template>
              </template>
            </template>
          </UChatMessages>

          <ConversationPrompt
            v-model="text"
            v-model:mode="mode"
            :active-run="activeRun"
            :has-open-question="hasOpenQuestion"
            :attachments="attachments.items.value"
            :sending="sending"
            :stopping="stopping"
            class="sticky bottom-0 z-10 rounded-b-none"
            @submit="send"
            @stop="stop"
            @attach="attach"
            @remove-attachment="attachments.remove"
          />
        </UContainer>
      </template>
    </UDashboardPanel>

    <UDashboardPanel
      v-if="isWide && filesPanelOpen"
      :id="`conversation-files-${conversationId}`"
      :default-size="22"
      :min-size="16"
      :max-size="40"
      resizable
    >
      <template #header>
        <UDashboardNavbar :title="$t('添付と成果物')">
          <template #right>
            <DBtn
              icon="i-lucide-x"
              variant="ghost"
              color="neutral"
              :aria-label="$t('閉じる')"
              @click="filesPanelOpen = false"
            />
          </template>
        </UDashboardNavbar>
      </template>
      <template #body>
        <ConversationFiles :conversation-id="conversationId" :items="files" :loading="filesLoading" />
      </template>
    </UDashboardPanel>

    <DDrawer v-if="!isWide" v-model:open="filesDrawerOpen" :title="$t('添付と成果物')">
      <template #content>
        <ConversationFiles :conversation-id="conversationId" :items="files" :loading="filesLoading" />
      </template>
    </DDrawer>
  </div>
</template>
