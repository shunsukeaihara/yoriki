<script setup lang="ts">
import DDialog from '../common/DDialog.vue'

/**
 * 入口（Webhook）の作成（連携の管理者。authorization.md §5.5）。タスクと名前だけを決め、イベントIDの場所・項目の
 * 取り出し・上限は、作った後の画面でサンプルのpayloadを試しながら決める。入口とタスクは1対1で、タスクは作成した後は
 * 変えられない。作成の応答にだけ鍵が入るので、閉じる前に表示する
 */
const emit = defineEmits<{ saved: [value: TriggerBinding] }>()
const api = useAPI()
const router = useRouter()
const toast = useToast()
const { t } = useI18n()

const dialog = ref<InstanceType<typeof DDialog> | null>(null)
const emptyForm = () => ({ task: null as string | null, name: '' })
const form = ref(emptyForm())
const created = ref<TriggerBindingKey | null>(null)
// 選んだタスクの入力の項目（作った後に取り出しを設定する目安として見せる）
const selectedTask = ref<TriggerTask | null>(null)

const onOpen = (open: boolean) => {
  if (open) {
    form.value = emptyForm()
    created.value = null
    selectedTask.value = null
    errors.value = {}
  }
}

const { onSubmit, loading, errors } = createSubmit({
  form,
  // 受け取り方は既定（推奨の形式・1MiB）で作り、作った後の画面で決める
  fn: (f) =>
    api.manage.triggerBindings().create({
      task: f.task!,
      name: f.name.trim(),
      eventIdPath: '',
      maxPayloadBytes: DEFAULT_MAX_PAYLOAD_KIB * 1024,
    }),
  onSuccess: (result) => {
    created.value = result
    emit('saved', result)
    toast.add({ title: t('保存しました'), color: 'success' })
  },
  onError: (err) => toast.add({ title: t('エラー'), description: makeErrorMsg(err), color: 'error' }),
})
const canPost = computed(() => Boolean(form.value.task && form.value.name.trim()))

const taskDescription = (item: unknown) => {
  const task = item as TriggerTask
  return task.department ? `${task.scopeDisplay}・${task.department.name}` : task.scopeDisplay
}

const openSettings = () => {
  const id = created.value?.id
  dialog.value?.closeDialog()
  if (id) router.push(`/manage/trigger_bindings/${id}`)
}
</script>

<template>
  <!-- 送信中は閉じられない（閉じて開き直すと、もう一度作れてしまう） -->
  <DDialog
    ref="dialog"
    :ui="{ content: 'sm:max-w-2xl' }"
    :dismissible="!loading"
    :close="!loading"
    @update:open="onOpen"
  >
    <template #trigger="{ on }">
      <slot name="trigger" :on="on" />
    </template>
    <template #header>{{ created ? $t('入口を追加しました') : $t('入口を追加') }}</template>
    <template #content>
      <div v-if="created" class="grid gap-3">
        <IntegrationIssuedSecret
          :label="$t('入口の鍵')"
          :value="created.key"
          :description="
            $t(
              '送り元に Authorization: Bearer で設定してもらいます。鍵はこの画面でしか表示できません。分からなくなったら、鍵を作り直します',
            )
          "
        />
        <div class="grid gap-1 text-sm">
          <span class="text-muted">{{ $t('送り元に設定するURL') }}</span>
          <code class="break-all rounded bg-elevated px-2 py-1.5 text-xs">{{ hookUrl(created.url) }}</code>
        </div>
      </div>
      <DFormStack v-else>
        <DFormAlert :errors="errors" />
        <DAutocomplete
          v-model="form.task"
          :label="$t('起動するタスク')"
          required
          :search-fn="api.manage.triggerBindings().tasks"
          :item-description="taskDescription"
          :clearable="false"
          :hint="
            $t(
              '部門・全社・システムのタスクのうち、まだ入口がないもの。作成した後は変更できません。タスクには、失敗を知らせる通知先が必要です',
            )
          "
          :error-messages="errors.task"
          @selected="(item) => (selectedTask = item as TriggerTask | null)"
        />
        <div v-if="selectedTask?.inputs.length" class="grid gap-1 text-sm">
          <span class="text-muted">{{
            $t('タスクの入力の項目（作った後に、payloadからの取り出しを設定します）')
          }}</span>
          <div class="flex flex-wrap gap-1">
            <UBadge v-for="input in selectedTask.inputs" :key="input.name" color="neutral" variant="outline">
              {{ input.label || input.name }}
              <span class="font-mono text-muted">{{ input.name }}</span>
            </UBadge>
          </div>
        </div>
        <DTextField
          v-model="form.name"
          :label="$t('名前')"
          required
          :hint="$t('送り元が分かる名前（例：経費SaaS）')"
          :error-messages="errors.name"
        />
      </DFormStack>
    </template>
    <template #footer="{ closeDialog }">
      <template v-if="created">
        <DBtn color="neutral" variant="ghost" @click="closeDialog()">{{ $t('閉じる') }}</DBtn>
        <DBtn @click="openSettings">{{ $t('取り出しを設定する') }}</DBtn>
      </template>
      <template v-else>
        <DBtn color="neutral" variant="ghost" :disabled="loading" @click="closeDialog()">
          {{ $t('キャンセル') }}
        </DBtn>
        <DBtn :disabled="!canPost" :loading="loading" @click="onSubmit">{{ $t('保存') }}</DBtn>
      </template>
    </template>
  </DDialog>
</template>
