<script setup lang="ts">
/**
 * 会話の画面（design.md §11）。表示は REST API で取得し、実行中の Run のストリームと、ログインユーザー宛ての通知を
 * 受けたら取得し直す（realtime.md §5）。画面を閉じても Run は続き、開き直せば同じ表示に戻る。
 * タスクの修正チャット（design.md §11.11）では、その中でタスクを動かし、動いている間の追加の指示と回答を送る。
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

// 入力欄の `/` の一覧。見える Skill から、使えるもの（公開中で停止していない）と編集できるものに分ける
const { items: visibleSkills, fetchAll: fetchSkills } = useFetchAll(() => api.skills().list({ pageSize: 200 }))
const usableSkills = computed(() => visibleSkills.value.filter((s) => s.version > 0 && !s.suspended))
const editableSkills = computed(() => visibleSkills.value.filter((s) => s.canEdit))
// 入力欄の `@` の一覧。機密の Connector は、個人情報・機密情報を扱う会話でだけ選べる
const { items: usableConnectors, fetchAll: fetchConnectors } = useFetchAll(() => api.connectors.list({ pageSize: 200 }))
const sensitive = computed(() => conversation.value?.handlesSensitiveData ?? false)
// Tool を使えないモデルでは Connector を使えないので、一覧に出さない（送ってもサーバーが断る）
const connectorChoices = computed(() =>
  conversation.value?.modelProfileInfo?.supportsTools ? usableConnectors.value : [],
)

// 入力欄の `/` の一覧に出す、チャットから起動できるタスク（authorization.md §5.3）
const { items: launchableTasks, fetchAll: fetchLaunchableTasks } = useFetchAll(() =>
  api.launchableTasks().list({ pageSize: 200 }),
)

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
await Promise.all([
  messages.refresh(),
  fetchRuns(),
  fetchFiles(),
  fetchSkills(),
  fetchConnectors(),
  fetchLaunchableTasks(),
])

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
      // Skill は、会話で作った・公開したものを次の依頼から `/` で選べるように読み直す
      await Promise.all([refreshConversation(), messages.refresh(), fetchRuns(), fetchFiles(), fetchSkills()])
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
// この会話から起動したタスクの Run（カードの状態と応答を出す。タスクの Run は別の会話で動く）
const launchedRunIds = computed(() => new Set(messages.items.value.flatMap((m) => (m.taskRun ? [m.taskRun.id] : []))))
// 別のタブや外部のスレッドから、この会話で新しい Run が始まった場合と、起動したタスクの Run の状態が変わった場合
useUserEvents(
  (event) => {
    if (event.type !== 'run.status') return
    if (event.conversation === conversationId && event.run !== activeRunId.value) sync()
    else if (launchedRunIds.value.has(event.run)) sync()
  },
  { onReconnect: () => sync() },
)

// --- タスクの起動 ---

const launchTask = ref<LaunchableTask | null>(null)
const launchOpen = ref(false)
const openLaunch = (task: LaunchableTask) => {
  launchTask.value = task
  launchOpen.value = true
}
// 版が変わったときは、候補（入力の項目）を読み直す。起動した・受け付けたかが分からないときは、カードを読み直す
const onLaunchConflicted = () => fetchLaunchableTasks()

// --- タスクの修正チャット（design.md §11.11） ---

const fixTask = computed(() => conversation.value?.task ?? null)
const fixOpen = ref(false)
const fixTaskDetail = ref<TaskDetail | null>(null)
const fixInitialValues = ref<TaskRunInputs | undefined>()
const openingFix = ref(false)
// タスクの今の定義（入力の項目）を読み、前の実行の入力を初期値にしてダイアログを開く（ファイルの項目は選び直す）
const openFix = async () => {
  if (!fixTask.value) return
  openingFix.value = true
  try {
    const detail = await api.tasks(fixTask.value.id).get()
    const last = runs.value.find((r) => r.taskInfo)
    let values: TaskRunInputs | undefined
    if (last) {
      const taskRun = await api.tasks(detail.id).runs(last.id).get()
      const kept = new Set((detail.inputs ?? []).filter((i) => i.type !== TaskInputType.FILE).map((i) => i.name))
      values = Object.fromEntries(
        taskRun.inputs.filter((i) => kept.has(i.name)).map((i) => [i.name, i.value as TaskRunInputs[string]]),
      )
    }
    fixTaskDetail.value = detail
    fixInitialValues.value = values
    fixOpen.value = true
  } catch (err) {
    toast.add({ title: t('タスクを読めませんでした'), description: makeErrorMsg(err), color: 'error' })
  } finally {
    openingFix.value = false
  }
}

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

// 修正チャットで動かしたタスクの Run の、最初の依頼（タスクの依頼文と入力から作ったもの）
const taskRunOf = (message: Message) => {
  if (message.role !== MessageRole.USER || message.instruction || !message.run) return null
  return runById.value.get(message.run)?.taskInfo ?? null
}

const instructionLabel = (message: Message) => {
  const instruction = message.instruction
  if (!instruction) return ''
  const kind = instruction.kind === InstructionKind.NEXT ? t('次の依頼') : t('追加の指示')
  return t('{kind}・{state}', { kind, state: instruction.stateDisplay })
}

// --- 操作 ---

const text = ref('')
const mode = ref<string>(MessageMode.AUTO)
const selectedSkills = ref<Skill[]>([])
const skillEdit = ref<SkillEditChoice | null>(null)
const selectedConnectors = ref<AppConnector[]>([])
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
        skills: selectedSkills.value.map((s) => s.id),
        connectors: selectedConnectors.value.map((c) => c.id),
        editSkill: skillEdit.value?.skill?.id ?? null,
        createSkill: skillEdit.value !== null && skillEdit.value.skill === null,
        clientMessageId,
        mode: mode.value as MessageWrite['mode'],
      })
    clientMessageId = crypto.randomUUID()
    text.value = ''
    mode.value = MessageMode.AUTO
    selectedSkills.value = []
    skillEdit.value = null
    selectedConnectors.value = []
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
              v-if="conversation && !fixTask"
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
        <UDashboardToolbar v-if="conversation && !fixTask" class="sm:hidden">
          <ConversationModelSettings :conversation="conversation" :models="models" @updated="onUpdated" />
        </UDashboardToolbar>
        <!-- 修正チャット：直すタスクと、もう一度動かす操作 -->
        <UDashboardToolbar v-if="fixTask">
          <div class="flex min-w-0 flex-1 flex-wrap items-center gap-2 text-sm">
            <UIcon name="i-lucide-messages-square" class="size-4 shrink-0 text-muted" />
            <span class="text-muted">{{ $t('修正チャット') }}</span>
            <NuxtLink v-if="!fixTask.deleted" :to="`/tasks/${fixTask.id}`" class="min-w-0 truncate text-primary">
              {{ fixTask.name }}
            </NuxtLink>
            <span v-else class="min-w-0 truncate">{{ $t('{name}（削除済み）', { name: fixTask.name }) }}</span>
            <UBadge color="neutral" variant="subtle" size="sm">{{ fixTask.scopeDisplay }}</UBadge>
          </div>
          <DBtn
            icon="i-lucide-play"
            size="sm"
            :disabled="Boolean(activeRun) || fixTask.deleted"
            :loading="openingFix"
            @click="openFix"
          >
            {{ $t('もう一度動かす') }}
          </DBtn>
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
                  <!-- この会話から起動したタスクの Run -->
                  <TaskLaunchCard v-else-if="item.message.taskRun" :run="item.message.taskRun" />
                  <!-- システムのメッセージ（次の依頼を始められなかった理由等） -->
                  <p
                    v-else-if="item.message.role === MessageRole.SYSTEM"
                    class="flex items-start gap-2 text-sm whitespace-pre-wrap text-muted"
                  >
                    <UIcon name="i-lucide-info" class="mt-0.5 size-4 shrink-0" />
                    <span>{{ item.message.body }}</span>
                  </p>
                  <template v-else>
                    <UBadge
                      v-if="taskRunOf(item.message)"
                      color="info"
                      variant="subtle"
                      size="sm"
                      icon="i-lucide-workflow"
                      class="mb-1"
                    >
                      {{ $t('タスクを動かしました（版 {number}）', { number: taskRunOf(item.message)?.number }) }}
                    </UBadge>
                    <UBadge
                      v-if="item.message.skillEdit"
                      color="warning"
                      variant="subtle"
                      size="sm"
                      :icon="item.message.skillEdit.id ? 'i-lucide-pencil' : 'i-lucide-plus'"
                      class="mb-1"
                    >
                      {{
                        item.message.skillEdit.id
                          ? $t('Skillの編集：{name}', { name: item.message.skillEdit.name })
                          : $t('新しいSkillを作る')
                      }}
                    </UBadge>
                    <ul v-if="item.message.skillsInfo.length" class="mb-1 flex flex-wrap gap-1">
                      <li v-for="skill in item.message.skillsInfo" :key="skill.id">
                        <UBadge color="primary" variant="subtle" size="sm" icon="i-lucide-sparkles">
                          {{ skill.name }}
                        </UBadge>
                      </li>
                    </ul>
                    <ul v-if="item.message.connectorsInfo.length" class="mb-1 flex flex-wrap gap-1">
                      <li v-for="connector in item.message.connectorsInfo" :key="connector.id">
                        <UBadge color="info" variant="subtle" size="sm" icon="i-lucide-database">
                          {{ connector.name }}
                        </UBadge>
                      </li>
                    </ul>
                    <!-- タスクの最初の依頼は、埋め込んだ入力の値を目立たせて出す -->
                    <p v-if="item.message.body && taskRunOf(item.message)">
                      <TaskPromptText :text="item.message.body" />
                    </p>
                    <p v-else-if="item.message.body" class="whitespace-pre-wrap">{{ item.message.body }}</p>
                  </template>
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
            v-model:selected-skills="selectedSkills"
            v-model:skill-edit="skillEdit"
            v-model:selected-connectors="selectedConnectors"
            :active-run="activeRun"
            :skills="usableSkills"
            :editable-skills="editableSkills"
            :connectors="connectorChoices"
            :tasks="launchableTasks"
            :sensitive="sensitive"
            :has-open-question="hasOpenQuestion"
            :attachments="attachments.items.value"
            :sending="sending"
            :stopping="stopping"
            :fix-chat="Boolean(fixTask)"
            :disabled="Boolean(fixTask) && !activeRun"
            class="sticky bottom-0 z-10 rounded-b-none"
            @submit="send"
            @stop="stop"
            @attach="attach"
            @remove-attachment="attachments.remove"
            @retry-attachments="attachments.upload(conversationId)"
            @launch="openLaunch"
          />
          <TaskLaunchDialog
            v-model:open="launchOpen"
            :task="launchTask"
            :conversation-id="conversationId"
            @started="syncNow"
            @unknown="syncNow"
            @conflicted="onLaunchConflicted"
          />
          <TaskFixChatDialog
            v-if="fixTask"
            v-model:open="fixOpen"
            :task="fixTaskDetail"
            :conversation-id="conversationId"
            :initial-values="fixInitialValues"
            @started="syncNow"
            @unknown="syncNow"
            @conflicted="syncNow"
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
