<script setup lang="ts">
import { AxiosError } from 'axios'
import DestinationForm from '~/components/destination/DestinationForm.vue'

const emit = defineEmits<{ changed: [] }>()
const route = useRoute()
const router = useRouter()
const api = useAPI()
const toast = useToast()
const confirm = useConfirmDialog()
const { t } = useI18n()
const { hasPermission } = usePermission()
const destinationId = route.params.destinationId as string

const {
  data: destination,
  error,
  refresh,
} = await useAsyncData(`destination-${destinationId}`, () => api.manage.destinations(destinationId).get())
if (error.value) throw createError(error.value) // 404 / 403 はエラー画面にする

const formRef = ref<InstanceType<typeof DestinationForm> | null>(null)
const tab = ref('settings')
const revisionsReloadKey = ref(0)
// 編集の間に他の人が版を作っていた（409）。最新の状態を読み込んでから編集し直す
const conflicted = ref(false)
// 作り直した署名の鍵。この画面を閉じるまで表示する（サーバーは二度と返さない）
const issuedSecret = ref<string | null>(null)
const busy = ref(false)

// 編集・停止・鍵の作り直しはサーバー側でも権限を確認し、なければ403になる
const canChange = computed(() => hasPermission(PermissionCode.CHANGE_DESTINATION))
const isWebhook = computed(() => destination.value?.kind === DestinationKind.WEBHOOK)

const close = () => router.push('/manage/destinations')

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
    title: t('出口を停止しますか？'),
    description: t('「{name}」へは、まだ送っていない配信も送りません。新しく受け付けるRunにも使いません', {
      name: destination.value?.name ?? '',
    }),
    confirmLabel: t('停止'),
    color: 'error',
  })
  if (!ok) return
  await run(async () => {
    destination.value = await api.manage.destinations(destinationId).suspend()
    toast.add({ title: t('停止しました'), color: 'success' })
  })
}
const onResume = () =>
  run(async () => {
    destination.value = await api.manage.destinations(destinationId).resume()
    toast.add({ title: t('停止を解きました'), color: 'success' })
  })

const onRotate = async () => {
  const ok = await confirm({
    title: t('署名の鍵を作り直しますか？'),
    description: t('次の送信から新しい鍵で署名します。受け手の設定を新しい鍵に変えるまで、署名の確かめに失敗します'),
    confirmLabel: t('作り直す'),
  })
  if (!ok) return
  await run(async () => {
    const { signingSecret, ...rest } = await api.manage.destinations(destinationId).rotateSecret()
    issuedSecret.value = signingSecret
    destination.value = rest
    toast.add({ title: t('署名の鍵を作り直しました'), color: 'success' })
  })
}

const onDelete = async () => {
  const ok = await confirm({
    title: t('出口を削除しますか？'),
    description: t('「{name}」へは、まだ送っていない配信も送りません。版の履歴は残ります', {
      name: destination.value?.name ?? '',
    }),
    confirmLabel: t('削除'),
    color: 'error',
  })
  if (!ok) return
  try {
    await api.manage.destinations(destinationId).delete()
    toast.add({ title: t('削除しました'), color: 'success' })
    emit('changed')
    close()
  } catch (err) {
    toast.add({ title: t('エラー'), description: makeErrorMsg(err), color: 'error' })
  }
}

const tabs = computed(() => [
  { label: t('設定'), value: 'settings', slot: 'settings' as const },
  { label: t('版の履歴'), value: 'revisions', slot: 'revisions' as const },
])
</script>

<template>
  <DDrawer
    :open="true"
    :title="destination?.name ?? $t('出口')"
    :description="destination ? $t('現在の版：{number}', { number: destination.version ?? 0 }) : undefined"
    :ui="{ content: 'sm:max-w-2xl' }"
    @update:open="(open) => !open && close()"
  >
    <template #content>
      <div class="grid gap-4">
        <UAlert
          v-if="destination?.suspended"
          color="error"
          variant="subtle"
          icon="i-lucide-pause"
          :title="$t('停止中です。この出口へは送りません')"
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
          v-if="issuedSecret"
          :label="$t('新しい署名の鍵')"
          :value="issuedSecret"
          :description="$t('受け手の設定を、この鍵に変えてください。鍵はこの画面を閉じると表示できません')"
        />
        <div v-if="destination?.secretRotated" class="text-xs text-muted">
          {{ $t('署名の鍵を作った日時：{date}', { date: formatDatetime(destination.secretRotated) }) }}
        </div>
        <UTabs v-model="tab" :items="tabs" variant="link" :unmount-on-hide="false">
          <template #settings>
            <div class="pt-4">
              <DestinationForm ref="formRef" v-model="destination" @updated="onUpdated" @failed="onFailed" />
            </div>
          </template>
          <template #revisions>
            <div class="pt-4">
              <DestinationRevisionList v-model:reload-key="revisionsReloadKey" :destination-id="destinationId" />
            </div>
          </template>
        </UTabs>
      </div>
    </template>
    <template #footer>
      <!-- 削除はサーバー側でも権限を確認し、権限がなければ403になる -->
      <DBtn v-if="hasPermission(PermissionCode.DELETE_DESTINATION)" color="error" variant="ghost" @click="onDelete">
        {{ $t('削除') }}
      </DBtn>
      <template v-if="canChange && destination">
        <DBtn v-if="destination.suspended" color="neutral" variant="outline" :loading="busy" @click="onResume">
          {{ $t('停止を解く') }}
        </DBtn>
        <DBtn v-else color="neutral" variant="outline" :loading="busy" @click="onSuspend">{{ $t('停止') }}</DBtn>
        <DBtn v-if="isWebhook" color="neutral" variant="outline" :loading="busy" @click="onRotate">
          {{ $t('署名の鍵を作り直す') }}
        </DBtn>
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
