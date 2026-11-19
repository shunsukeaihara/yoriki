<script setup lang="ts">
import ChannelConnectionForm from '~/components/channel-connection/ChannelConnectionForm.vue'

const emit = defineEmits<{ changed: [] }>()
const route = useRoute()
const router = useRouter()
const api = useAPI()
const toast = useToast()
const confirm = useConfirmDialog()
const { t } = useI18n()
const { hasPermission } = usePermission()
const channelConnectionId = route.params.channelConnectionId as string

const { data: connection, error } = await useAsyncData(`channel-connection-${channelConnectionId}`, () =>
  api.manage.channelConnections(channelConnectionId).get(),
)
if (error.value) throw createError(error.value) // 404 / 403 はエラー画面にする

const formRef = ref<InstanceType<typeof ChannelConnectionForm> | null>(null)
const busy = ref(false)
// 編集・停止はサーバー側でも権限を確認し、なければ403になる
const canChange = computed(() => hasPermission(PermissionCode.CHANGE_CHANNEL_CONNECTION))

const close = () => router.push('/manage/channel_connections')

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
    title: t('連携の接続を停止しますか？'),
    description: t('「{name}」を使う出口へは投稿せず、Slackからの回答も受け付けません', {
      name: connection.value?.name ?? '',
    }),
    confirmLabel: t('停止'),
    color: 'error',
  })
  if (!ok) return
  await run(async () => {
    connection.value = await api.manage.channelConnections(channelConnectionId).suspend()
    toast.add({ title: t('停止しました'), color: 'success' })
  })
}
const onResume = () =>
  run(async () => {
    connection.value = await api.manage.channelConnections(channelConnectionId).resume()
    toast.add({ title: t('停止を解きました'), color: 'success' })
  })

const onDelete = async () => {
  const ok = await confirm({
    title: t('連携の接続を削除しますか？'),
    description: t('この接続を使う出口が残っていると削除できません。先に出口を削除してください'),
    confirmLabel: t('削除'),
    color: 'error',
  })
  if (!ok) return
  try {
    await api.manage.channelConnections(channelConnectionId).delete()
    toast.add({ title: t('削除しました'), color: 'success' })
    emit('changed')
    close()
  } catch (err) {
    // 出口が使っている接続は400で断られ、その理由を表示する
    toast.add({ title: t('エラー'), description: makeErrorMsg(err), color: 'error' })
  }
}
</script>

<template>
  <DDrawer
    :open="true"
    :title="connection?.name ?? $t('連携の接続')"
    :ui="{ content: 'sm:max-w-2xl' }"
    @update:open="(open) => !open && close()"
  >
    <template #content>
      <div class="grid gap-6">
        <UAlert
          v-if="connection?.suspended"
          color="error"
          variant="subtle"
          icon="i-lucide-pause"
          :title="$t('停止中です。この接続を使う出口へは投稿せず、回答も受け付けません')"
        />
        <ChannelConnectionReceiveUrl v-if="connection" :url="connection.receiveUrl" />
        <USeparator />
        <ChannelConnectionForm ref="formRef" v-model="connection" @updated="emit('changed')" />
      </div>
    </template>
    <template #footer>
      <!-- 削除はサーバー側でも権限を確認し、権限がなければ403になる -->
      <DBtn
        v-if="hasPermission(PermissionCode.DELETE_CHANNEL_CONNECTION)"
        color="error"
        variant="ghost"
        @click="onDelete"
      >
        {{ $t('削除') }}
      </DBtn>
      <template v-if="canChange && connection">
        <DBtn v-if="connection.suspended" color="neutral" variant="outline" :loading="busy" @click="onResume">
          {{ $t('停止を解く') }}
        </DBtn>
        <DBtn v-else color="neutral" variant="outline" :loading="busy" @click="onSuspend">{{ $t('停止') }}</DBtn>
      </template>
      <div class="flex-1" />
      <DBtn v-if="canChange" :disabled="!formRef?.canPost" :loading="formRef?.loading" @click="formRef?.onSubmit()">
        {{ $t('更新') }}
      </DBtn>
    </template>
  </DDrawer>
</template>
