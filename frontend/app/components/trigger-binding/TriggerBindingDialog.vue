<script setup lang="ts">
import DDialog from '../common/DDialog.vue'

/**
 * 入口（Webhook）の作成（タスクを管理できる人。authorization.md §5.5）。名前だけを決め、イベントIDの場所・項目の
 * 取り出し・上限は、作った後の画面でサンプルのpayloadを試しながら決める。結果の通知はタスクに1つなので、どの入口から
 * 起動したRunの結果も同じ出口へ届くことを示す。作成の応答にだけ鍵が入るので、閉じる前に表示する
 */
const props = defineProps<{ task: TaskDetail }>()
const emit = defineEmits<{ saved: [value: TriggerBinding] }>()
const api = useAPI()
const router = useRouter()
const toast = useToast()
const { t } = useI18n()

const dialog = ref<InstanceType<typeof DDialog> | null>(null)
const emptyForm = () => ({ name: '' })
const form = ref(emptyForm())
const created = ref<TriggerBindingKey | null>(null)

const onOpen = (open: boolean) => {
  if (open) {
    form.value = emptyForm()
    created.value = null
    errors.value = {}
  }
}

const { onSubmit, loading, errors } = createSubmit({
  form,
  // 受け取り方は既定（推奨の形式・1MiB）で作り、作った後の画面で決める
  fn: (f) =>
    api
      .tasks(props.task.id)
      .triggers()
      .create({
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
const canPost = computed(() => Boolean(form.value.name.trim()))

const openSettings = () => {
  const id = created.value?.id
  dialog.value?.closeDialog()
  if (id) router.push(`/tasks/${props.task.id}/triggers/${id}`)
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
        <DTextField
          v-model="form.name"
          :label="$t('名前')"
          required
          :hint="$t('送り元が分かる名前（例：経費SaaS）。送り元ごとに入口を分けます')"
          :error-messages="errors.name"
        />
        <!-- サーバーの入力の誤り（失敗を知らせる通知先がない等）は、タスクの設定の問題なので欄の外に出す -->
        <p v-if="errors.task" class="text-sm text-error">{{ errors.task.join('、') }}</p>
        <div class="grid gap-1 text-sm" data-testid="trigger-destination">
          <span class="text-muted">{{ $t('結果の届け先') }}</span>
          <span v-if="task.destinationInfo">{{ task.destinationInfo.name }}</span>
          <span v-else class="text-warning">{{
            $t('通知先がありません。Webhookで起動するタスクには、失敗を知らせる通知先が必要です')
          }}</span>
          <span class="text-xs text-muted">{{
            $t(
              '結果の通知はタスクに1つなので、どの入口から起動しても同じ通知先に届きます。送り元ごとに届け先を分けたいときは、タスクを分けます',
            )
          }}</span>
        </div>
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
