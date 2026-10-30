<script setup lang="ts">
import { AxiosError } from 'axios'
import TriggerBindingForm from '~/components/trigger-binding/TriggerBindingForm.vue'

const emit = defineEmits<{ changed: [] }>()
const route = useRoute()
const router = useRouter()
const api = useAPI()
const toast = useToast()
const confirm = useConfirmDialog()
const { t } = useI18n()
const { hasPermission } = usePermission()
const bindingId = route.params.bindingId as string

const {
  data: binding,
  error,
  refresh,
} = await useAsyncData(`trigger-binding-${bindingId}`, () => api.manage.triggerBindings(bindingId).get())
if (error.value) throw createError(error.value) // 404 / 403 はエラー画面にする

const formRef = ref<InstanceType<typeof TriggerBindingForm> | null>(null)
const tab = ref('settings')
const revisionsReloadKey = ref(0)
const receiptsReloadKey = ref(0)
// 編集の間に他の人が版を作っていた（409）。最新の状態を読み込んでから編集し直す
const conflicted = ref(false)
// 作り直した鍵。この画面を閉じるまで表示する（サーバーは二度と返さない）
const issuedKey = ref<string | null>(null)
const busy = ref(false)

// 編集・停止・鍵の作り直しはサーバー側でも権限を確認し、なければ403になる
const canChange = computed(() => hasPermission(PermissionCode.CHANGE_TRIGGER_BINDING))
// 試すのは、編集中の受け取り方（保存していないもの）。フォームがまだなければ今の版
const previewSettings = computed(
  () =>
    formRef.value?.settings ?? {
      eventIdPath: binding.value?.eventIdPath ?? '',
      inputs: [...(binding.value?.inputs ?? [])],
    },
)

const close = () => router.push('/manage/trigger_bindings')

const onUpdated = () => {
  conflicted.value = false
  revisionsReloadKey.value = Date.now()
  emit('changed')
}
const onFailed = (err: unknown) => {
  conflicted.value = err instanceof AxiosError && err.response?.status === 409
}
// 他の人の版を読み込み、未保存の入力を捨てて合わせ直す
const reloadLatest = async () => {
  await refresh()
  if (error.value) {
    toast.add({ title: t('エラー'), description: makeErrorMsg(error.value), color: 'error' })
    return
  }
  formRef.value?.reset()
  conflicted.value = false
  revisionsReloadKey.value = Date.now()
}

const run = async (fn: () => Promise<void>) => {
  busy.value = true
  try {
    await fn()
    emit('changed')
  } catch (err) {
    toast.add({ title: t('エラー'), description: makeErrorMsg(err), color: 'error' })
  } finally {
    busy.value = false
  }
}

const onSuspend = async () => {
  const ok = await confirm({
    title: t('入口を停止しますか？'),
    description: t('「{name}」は新しい要求を受け付けず、送り元には403を返します', { name: binding.value?.name ?? '' }),
    confirmLabel: t('停止'),
    color: 'error',
  })
  if (!ok) return
  await run(async () => {
    binding.value = await api.manage.triggerBindings(bindingId).suspend()
    toast.add({ title: t('停止しました'), color: 'success' })
  })
}
const onResume = () =>
  run(async () => {
    binding.value = await api.manage.triggerBindings(bindingId).resume()
    toast.add({ title: t('停止を解きました'), color: 'success' })
  })

const onRotate = async () => {
  const ok = await confirm({
    title: t('鍵を作り直しますか？'),
    description: t('前の鍵はすぐに使えなくなります。送り元の設定を新しい鍵に変えるまで、要求は401で断ります'),
    confirmLabel: t('作り直す'),
  })
  if (!ok) return
  await run(async () => {
    const { key, ...rest } = await api.manage.triggerBindings(bindingId).rotateKey()
    issuedKey.value = key
    binding.value = rest
    toast.add({ title: t('鍵を作り直しました'), color: 'success' })
  })
}

const onDelete = async () => {
  const ok = await confirm({
    title: t('入口を削除しますか？'),
    description: t('「{name}」の鍵はすぐに使えなくなります。受け付けたRunは止めません', {
      name: binding.value?.name ?? '',
    }),
    confirmLabel: t('削除'),
    color: 'error',
  })
  if (!ok) return
  try {
    await api.manage.triggerBindings(bindingId).delete()
    toast.add({ title: t('削除しました'), color: 'success' })
    emit('changed')
    close()
  } catch (err) {
    toast.add({ title: t('エラー'), description: makeErrorMsg(err), color: 'error' })
  }
}

const tabs = computed(() => [
  { label: t('設定'), value: 'settings', slot: 'settings' as const },
  { label: t('取り出しを試す'), value: 'preview', slot: 'preview' as const },
  { label: t('受付の履歴'), value: 'receipts', slot: 'receipts' as const },
  { label: t('版の履歴'), value: 'revisions', slot: 'revisions' as const },
])
</script>

<template>
  <DDrawer
    :open="true"
    :title="binding?.name ?? $t('入口')"
    :description="binding ? $t('現在の版：{number}', { number: binding.version ?? 0 }) : undefined"
    :ui="{ content: 'sm:max-w-3xl' }"
    @update:open="(open) => !open && close()"
  >
    <template #content>
      <div v-if="binding" class="grid gap-4">
        <UAlert
          v-if="binding.suspended"
          color="error"
          variant="subtle"
          icon="i-lucide-pause"
          :title="$t('停止中です。新しい要求を受け付けません')"
        />
        <UAlert
          v-if="conflicted"
          color="warning"
          variant="subtle"
          icon="i-lucide-refresh-cw"
          :title="$t('編集の間に、他の人が新しい版を作りました')"
          :actions="[
            { label: $t('最新の状態を読み込む'), color: 'warning', variant: 'outline', onClick: reloadLatest },
          ]"
        />
        <IntegrationIssuedSecret
          v-if="issuedKey"
          :label="$t('新しい鍵')"
          :value="issuedKey"
          :description="$t('送り元の設定を、この鍵に変えてください。鍵はこの画面を閉じると表示できません')"
        />
        <dl class="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-sm">
          <dt class="text-muted">{{ $t('起動するタスク') }}</dt>
          <dd>
            {{ binding.taskInfo.name }}
            <span class="text-xs text-muted">
              （{{ binding.taskInfo.scopeDisplay
              }}<template v-if="binding.taskInfo.department">・{{ binding.taskInfo.department.name }}</template
              >）
            </span>
            <UBadge v-if="binding.taskInfo.suspended" color="error" variant="subtle" size="sm" class="ml-1">
              {{ $t('タスクは停止中') }}
            </UBadge>
          </dd>
          <dt class="text-muted">{{ $t('URL') }}</dt>
          <dd>
            <code class="break-all text-xs">POST {{ hookUrl(binding.url) }}</code>
          </dd>
          <dt class="text-muted">{{ $t('鍵') }}</dt>
          <dd>
            <code class="text-xs">Authorization: Bearer {{ binding.keyPrefix }}…</code>
            <span v-if="binding.keyRotated" class="ml-2 text-xs text-muted">
              {{ $t('作り直した日時：{date}', { date: formatDatetime(binding.keyRotated) }) }}
            </span>
          </dd>
        </dl>
        <UTabs v-model="tab" :items="tabs" variant="link" :unmount-on-hide="false">
          <template #settings>
            <div class="pt-4">
              <TriggerBindingForm ref="formRef" v-model="binding" @updated="onUpdated" @failed="onFailed" />
            </div>
          </template>
          <template #preview>
            <div class="pt-4">
              <TriggerBindingPreview :binding-id="bindingId" :settings="previewSettings" />
            </div>
          </template>
          <template #receipts>
            <div class="pt-4">
              <TriggerReceiptList v-model:reload-key="receiptsReloadKey" :binding-id="bindingId" />
            </div>
          </template>
          <template #revisions>
            <div class="pt-4">
              <TriggerBindingRevisionList v-model:reload-key="revisionsReloadKey" :binding-id="bindingId" />
            </div>
          </template>
        </UTabs>
      </div>
    </template>
    <template #footer>
      <!-- 削除はサーバー側でも権限を確認し、権限がなければ403になる -->
      <DBtn v-if="hasPermission(PermissionCode.DELETE_TRIGGER_BINDING)" color="error" variant="ghost" @click="onDelete">
        {{ $t('削除') }}
      </DBtn>
      <template v-if="canChange && binding">
        <DBtn v-if="binding.suspended" color="neutral" variant="outline" :loading="busy" @click="onResume">
          {{ $t('停止を解く') }}
        </DBtn>
        <DBtn v-else color="neutral" variant="outline" :loading="busy" @click="onSuspend">{{ $t('停止') }}</DBtn>
        <DBtn color="neutral" variant="outline" :loading="busy" @click="onRotate">{{ $t('鍵を作り直す') }}</DBtn>
      </template>
      <div class="flex-1" />
      <DBtn
        v-if="canChange && tab === 'settings'"
        :disabled="!formRef?.canPost"
        :loading="formRef?.loading"
        @click="formRef?.onSubmit()"
      >
        {{ $t('更新') }}
      </DBtn>
    </template>
  </DDrawer>
</template>
