<script setup lang="ts">
import LlmConnectionForm from '~/components/llm-connection/LlmConnectionForm.vue'

const emit = defineEmits<{ changed: [] }>()
const route = useRoute()
const router = useRouter()
const api = useAPI()
const toast = useToast()
const confirm = useConfirmDialog()
const { t } = useI18n()
const { hasPermission } = usePermission()
const llmConnectionId = route.params.llmConnectionId as string

const { data: connection, error } = await useAsyncData(`llm-connection-${llmConnectionId}`, () =>
  api.manage.llmConnections(llmConnectionId).get(),
)
if (error.value) throw createError(error.value) // 404 / 403 はエラー画面にする

const formRef = ref<InstanceType<typeof LlmConnectionForm> | null>(null)
const close = () => router.push('/manage/llm_connections')

const onDelete = async () => {
  const ok = await confirm({ title: t('LLMの接続を削除しますか？'), confirmLabel: t('削除'), color: 'error' })
  if (!ok) return
  try {
    await api.manage.llmConnections(llmConnectionId).delete()
    toast.add({ title: t('削除しました'), color: 'success' })
    emit('changed')
    close()
  } catch (err) {
    // モデル設定が使っている接続は409で断られ、その理由を表示する
    toast.add({ title: t('エラー'), description: makeErrorMsg(err), color: 'error' })
  }
}
</script>

<template>
  <DDrawer :open="true" :title="$t('LLMの接続')" @update:open="(open) => !open && close()">
    <template #content>
      <div class="grid gap-6">
        <LlmConnectionForm ref="formRef" v-model="connection" @updated="emit('changed')" />
        <USeparator />
        <LlmConnectionCheckButton :connection-id="llmConnectionId" />
      </div>
    </template>
    <template #footer>
      <!-- 削除はサーバー側でも権限を確認し、権限がなければ403になる -->
      <DBtn v-if="hasPermission(PermissionCode.CHANGE_LLM_CONNECTION)" color="error" variant="ghost" @click="onDelete">
        {{ $t('削除') }}
      </DBtn>
      <div class="flex-1" />
      <DBtn :disabled="!formRef?.canPost" :loading="formRef?.loading" @click="formRef?.onSubmit()">
        {{ $t('更新') }}
      </DBtn>
    </template>
  </DDrawer>
</template>
