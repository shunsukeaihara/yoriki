<script setup lang="ts">
/**
 * タスクの詳細（design.md §11.9）。中身の編集（保存のたびに版ができる）、手動実行、実行の履歴、版の履歴、停止・再開・
 * 削除。タスクの定義は管理できる人だけが見られ（authorization.md §5.3）、見られる人はすべての操作ができる。
 * 入口（Webhook）と通知先の登録は連携の管理者が管理画面で行い、ここでは見るだけ・選ぶだけ
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

const tabs = computed(() => [
  { label: t('設定'), value: 'settings', slot: 'settings' as const },
  { label: t('実行の履歴'), value: 'runs', slot: 'runs' as const },
  { label: t('版の履歴'), value: 'revisions', slot: 'revisions' as const },
])
const tab = ref('settings')
const runsReloadKey = ref(0)
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

const onStarted = () => {
  tab.value = 'runs'
  runsReloadKey.value = Date.now()
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
            <TaskRunDialog :task="task" @started="onStarted" @conflicted="reloadAll" @unknown="onStarted">
              <template #trigger="{ on }">
                <DBtn
                  icon="i-lucide-play"
                  :disabled="!task.canRun || formRef?.dirty"
                  :aria-label="$t('実行')"
                  v-on="on"
                >
                  <span class="hidden sm:inline">{{ $t('実行') }}</span>
                </DBtn>
              </template>
            </TaskRunDialog>
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
          :title="$t('今の版のままでは実行できません。設定を直して保存してください')"
          :description="task.problems.join('、')"
        />
        <div v-if="task.triggers.length" class="grid gap-1 rounded-md border border-default p-3 text-sm">
          <p class="font-medium">{{ $t('入口') }}</p>
          <div v-for="trigger in task.triggers" :key="trigger.id" class="flex flex-wrap items-center gap-2">
            <UIcon name="i-lucide-webhook" />
            <span>{{ trigger.name }}</span>
            <code class="text-xs text-muted">{{ trigger.keyPrefix }}…</code>
            <UBadge v-if="trigger.suspended" color="error" variant="subtle" size="sm">{{ $t('停止中') }}</UBadge>
          </div>
          <p class="text-xs text-muted">{{ $t('入口は連携の管理者が管理画面で作り、鍵を管理します') }}</p>
        </div>

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
              <div class="flex flex-wrap items-center justify-end gap-2 border-t border-default pt-4">
                <span v-if="formRef?.dirty" class="text-xs text-warning">{{ $t('保存していない変更があります') }}</span>
                <DBtn :disabled="!formRef?.canPost" :loading="formRef?.loading" @click="formRef?.onSubmit()">
                  {{ $t('保存') }}
                </DBtn>
              </div>
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
