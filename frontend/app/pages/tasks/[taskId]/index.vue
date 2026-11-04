<script setup lang="ts">
/**
 * タスクの詳細（design.md §11.9）。中身の編集（保存のたびに版ができる）、手動実行、実行の履歴、版の履歴、停止・再開・
 * 削除、修正チャット（design.md §11.11）。タスクの定義は管理できる人だけが見られ（authorization.md §5.3）、見られる人は
 * すべての操作ができる。
 * 入口（Webhook）はここで作る（authorization.md §5.5）。通知先の登録は連携の管理者が管理画面で行い、ここでは選ぶだけ
 */
import { AxiosError } from 'axios'
import TaskForm from '~/components/task/TaskForm.vue'

const route = useRoute()
const api = useAPI()
const toast = useToast()
const confirm = useConfirmDialog()
const { t } = useI18n()
const taskId = route.params.taskId as string

const { data: task, error } = await useAsyncData(`task-${taskId}`, () => api.tasks(taskId).get())
if (error.value) throw createError(error.value) // 404 / 403 はエラー画面にする

// システムのタスクは管理画面の一覧から開く
const listPath = computed(() => (task.value?.scope === TaskScope.SYSTEM ? '/manage/system_tasks' : '/tasks'))

// 個人のタスクにはWebhookの入口を付けない（authorization.md §5.4）
const tabs = computed(() => [
  { label: t('設定'), value: 'settings', slot: 'settings' as const },
  ...(task.value?.scope === TaskScope.PERSONAL ? [] : [{ label: t('入口'), value: 'triggers', slot: 'triggers' as const }]),
  { label: t('実行の履歴'), value: 'runs', slot: 'runs' as const },
  { label: t('版の履歴'), value: 'revisions', slot: 'revisions' as const },
])
// 入口の画面から戻るときは ?tab=triggers で開く
const queryTab = route.query.tab as string | undefined
const tab = ref(tabs.value.some((item) => item.value === queryTab) ? queryTab! : 'settings')
const runsReloadKey = ref(0)
const triggersReloadKey = ref(0)
const revisionsReloadKey = ref(0)

const formRef = ref<InstanceType<typeof TaskForm> | null>(null)
// 編集の間に他の人が版を作った（409）・タスクが消えた（404）。最新の状態を読み込んでから編集し直す
const conflicted = ref(false)
const onFailed = (err: unknown) => {
  const status = err instanceof AxiosError ? err.response?.status : undefined
  conflicted.value = status === 409 || status === 404
}
const onUpdated = (value: TaskDetail) => {
  task.value = value
  conflicted.value = false
  revisionsReloadKey.value = Date.now()
}

// 最新の状態を読み込む。読めなければ、入力中の内容と競合の表示を残す（古い定義を最新に見せない）
const reloadAll = async () => {
  try {
    task.value = await api.tasks(taskId).get()
  } catch (err) {
    toast.add({ title: t('エラー'), description: makeErrorMsg(err), color: 'error' })
    return
  }
  await nextTick()
  if (task.value) formRef.value?.reset(task.value)
  conflicted.value = false
  runsReloadKey.value = Date.now()
  revisionsReloadKey.value = Date.now()
}
const reloadLatest = async () => {
  if (formRef.value?.dirty) {
    const ok = await confirm({
      title: t('保存していない変更を捨てて、最新の状態を読み込みますか？'),
      confirmLabel: t('読み込む'),
      color: 'error',
    })
    if (!ok) return
  }
  await reloadAll()
}

// 手動実行が版の違いで断られた。読み直せるまで実行を止め、最新の状態を読み込む操作を出す
const onRunConflicted = async () => {
  conflicted.value = true
  await reloadAll()
}

// 入口を足したら、タスクの入口の要約（警告・一覧の表示）も読み直す。編集中のフォームは task の変更では書き換わらない
const onTriggersChanged = async () => {
  try {
    task.value = await api.tasks(taskId).get()
  } catch (err) {
    toast.add({ title: t('エラー'), description: makeErrorMsg(err), color: 'error' })
  }
}
// 鍵の作り直しを確かめる入口（authorization.md §5.5）
const triggersToRotate = computed(() => (task.value?.triggers ?? []).filter((item) => item.keyNeedsRotation))

const onStarted = () => {
  tab.value = 'runs'
  runsReloadKey.value = Date.now()
}

// 修正チャットを始めたら、その会話を開く
const listStore = useConversationListStore()
const onFixStarted = async (run: Run) => {
  listStore.reload().catch(() => {})
  await navigateTo(`/conversations/${run.conversation}`)
}

const changing = ref(false)
const onSuspend = async () => {
  if (!task.value) return
  if (!task.value.suspended) {
    const ok = await confirm({
      title: t('タスクを停止しますか？'),
      description: t(
        '新しい実行を受け付けず、順番待ち・実行中のRunも次の区切りで止めます。Webhookの入口からの要求も断ります',
      ),
      confirmLabel: t('停止'),
      color: 'error',
    })
    if (!ok) return
  }
  changing.value = true
  try {
    const value = task.value.suspended ? await api.tasks(taskId).resume() : await api.tasks(taskId).suspend()
    task.value = value
    toast.add({ title: value.suspended ? t('停止しました') : t('再開しました'), color: 'success' })
  } catch (err) {
    toast.add({ title: t('エラー'), description: makeErrorMsg(err), color: 'error' })
  } finally {
    changing.value = false
  }
}

// チャットから起動できるか（版を作らずにすぐ切り替える。authorization.md §5.3）。システムのタスクには付けない
const chatLaunchLabel = computed(() => {
  switch (task.value?.scope) {
    case TaskScope.PERSONAL:
      return t('自分が、会話の入力欄の / から起動できます。')
    case TaskScope.DEPARTMENT:
      return t('部門に所属する人が、会話の入力欄の / から自分の権限で起動できます。')
    default:
      return t('全社員が、会話の入力欄の / から自分の権限で起動できます。')
  }
})
const changingChatLaunch = ref(false)
const onChatLaunch = async (value: boolean) => {
  if (!task.value) return
  changingChatLaunch.value = true
  try {
    const updated = await api.tasks(taskId).chatLaunch({ chatLaunchable: value })
    task.value = updated
    toast.add({
      title: updated.chatLaunchable ? t('チャットから起動できるようにしました') : t('チャットからの起動を止めました'),
      color: 'success',
    })
  } catch (err) {
    toast.add({ title: t('エラー'), description: makeErrorMsg(err), color: 'error' })
  } finally {
    changingChatLaunch.value = false
  }
}

const onDelete = async () => {
  const ok = await confirm({
    title: t('タスクを削除しますか？'),
    description: t('順番待ち・実行中のRunも次の区切りで止まり、Webhookの入口も使えなくなります'),
    confirmLabel: t('削除'),
    color: 'error',
  })
  if (!ok) return
  try {
    await api.tasks(taskId).delete()
    toast.add({ title: t('削除しました'), color: 'success' })
    await navigateTo(listPath.value)
  } catch (err) {
    toast.add({ title: t('エラー'), description: makeErrorMsg(err), color: 'error' })
  }
}
</script>

<template>
  <UDashboardPanel :id="`task-${taskId}`">
    <template #header>
      <UDashboardNavbar :title="task?.name">
        <template #leading>
          <DBtn :to="listPath" color="neutral" variant="ghost" icon="i-lucide-arrow-left" :aria-label="$t('一覧へ')" />
        </template>
        <template #right>
          <template v-if="task">
            <TaskRunDialog :task="task" @started="onStarted" @conflicted="onRunConflicted" @unknown="onStarted">
              <template #trigger="{ on }">
                <DBtn
                  icon="i-lucide-play"
                  :disabled="!task.canRun || formRef?.dirty || conflicted"
                  :aria-label="$t('実行')"
                  v-on="on"
                >
                  <span class="hidden sm:inline">{{ $t('実行') }}</span>
                </DBtn>
              </template>
            </TaskRunDialog>
            <TaskFixChatDialog :task="task" @started="onFixStarted" @conflicted="onRunConflicted">
              <template #trigger="{ on }">
                <UTooltip :text="$t('会話の中でタスクを動かし、作業を見ながら直します（結果は通知先に送りません）')">
                  <DBtn
                    icon="i-lucide-messages-square"
                    variant="outline"
                    :disabled="!task.version || formRef?.dirty || conflicted"
                    :aria-label="$t('修正チャット')"
                    v-on="on"
                  >
                    <span class="hidden sm:inline">{{ $t('修正チャット') }}</span>
                  </DBtn>
                </UTooltip>
              </template>
            </TaskFixChatDialog>
            <DBtn
              :color="task.suspended ? 'primary' : 'error'"
              variant="ghost"
              :icon="task.suspended ? 'i-lucide-play-circle' : 'i-lucide-octagon-pause'"
              :loading="changing"
              :aria-label="task.suspended ? $t('再開') : $t('停止')"
              @click="onSuspend"
            >
              <span class="hidden sm:inline">{{ task.suspended ? $t('再開') : $t('停止') }}</span>
            </DBtn>
            <DBtn color="error" variant="ghost" icon="i-lucide-trash-2" :aria-label="$t('削除')" @click="onDelete">
              <span class="hidden sm:inline">{{ $t('削除') }}</span>
            </DBtn>
          </template>
        </template>
      </UDashboardNavbar>
    </template>

    <template #body>
      <UContainer v-if="task" class="grid max-w-4xl min-w-0 gap-4">
        <div class="flex flex-wrap items-center gap-2 text-sm">
          <UBadge color="neutral" variant="subtle">{{ task.scopeDisplay }}</UBadge>
          <span v-if="task.scope !== TaskScope.COMPANY" class="text-muted">{{ taskOwnerLabel(task, t) }}</span>
          <UBadge color="primary" variant="subtle">{{ $t('版 {number}', { number: task.version }) }}</UBadge>
          <UBadge v-if="task.handlesSensitiveData" color="warning" variant="subtle" icon="i-lucide-shield">
            {{ $t('機密') }}
          </UBadge>
        </div>
        <UAlert
          v-if="task.suspended"
          color="error"
          variant="subtle"
          icon="i-lucide-octagon-pause"
          :title="$t('停止中です。手動実行もWebhookの入口からの要求も受け付けません')"
        />
        <UAlert
          v-if="task.problems.length"
          color="warning"
          variant="subtle"
          icon="i-lucide-triangle-alert"
          :title="
            $t(
              '今の版に、保存のときの検査に通らない設定があります。実行が断られることがあるので、直して保存してください',
            )
          "
          :description="task.problems.join('、')"
        />
        <UAlert
          v-if="triggersToRotate.length"
          color="warning"
          variant="subtle"
          icon="i-lucide-key-round"
          :title="$t('鍵の作り直しを確かめてください')"
          :description="
            $t(
              '鍵を作った人が今はこのタスクを管理できない、または作った人の記録がない入口があります：{names}',
              { names: triggersToRotate.map((item) => item.name).join('、') },
            )
          "
          :actions="[{ label: $t('入口を見る'), color: 'warning', variant: 'outline', onClick: () => (tab = 'triggers') }]"
        />

        <div v-if="task.scope !== TaskScope.SYSTEM" class="grid gap-1 rounded-md border border-default p-3 text-sm">
          <DSwitch
            :model-value="task.chatLaunchable"
            :label="$t('チャットから起動できる')"
            :disabled="changingChatLaunch"
            @update:model-value="onChatLaunch"
          />
          <p class="text-xs text-muted">
            {{ chatLaunchLabel }}
            <template v-if="task.scope !== TaskScope.PERSONAL">{{
              $t(
                '起動した人が使うSkill・Connectorをすべて使えなければ断ります。入力と処理の記録はタスクの管理者も見られます',
              )
            }}</template>
          </p>
        </div>

        <UAlert
          v-if="conflicted"
          color="warning"
          variant="subtle"
          icon="i-lucide-refresh-cw"
          :title="$t('編集の間に、他の人がタスクを保存しました')"
          :actions="[
            { label: $t('最新の状態を読み込む'), color: 'warning', variant: 'outline', onClick: reloadLatest },
          ]"
        />
        <UTabs
          v-model="tab"
          :items="tabs"
          variant="link"
          :unmount-on-hide="false"
          :ui="{ list: 'overflow-x-auto', trigger: 'shrink-0', root: 'min-w-0' }"
        >
          <template #settings>
            <div class="grid gap-4 pt-4">
              <TaskForm ref="formRef" :task="task" @updated="onUpdated" @failed="onFailed" />
              <div class="flex flex-wrap items-center justify-end gap-2 border-t border-default pt-4">
                <span v-if="formRef?.dirty" class="text-xs text-warning">{{ $t('保存していない変更があります') }}</span>
                <DBtn :disabled="!formRef?.canPost" :loading="formRef?.loading" @click="formRef?.onSubmit()">
                  {{ $t('保存') }}
                </DBtn>
              </div>
            </div>
          </template>

          <template #triggers>
            <div class="pt-4">
              <TriggerBindingList v-model:reload-key="triggersReloadKey" :task="task" @changed="onTriggersChanged" />
            </div>
          </template>

          <template #runs>
            <div class="pt-4">
              <TaskRunList v-model:reload-key="runsReloadKey" :task-id="taskId" />
            </div>
          </template>

          <template #revisions>
            <div class="pt-4">
              <TaskRevisionList v-model:reload-key="revisionsReloadKey" :task-id="taskId" :current="task.version" />
            </div>
          </template>
        </UTabs>
      </UContainer>
    </template>
  </UDashboardPanel>
</template>
