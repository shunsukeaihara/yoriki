<script setup lang="ts">
import DatabaseConnectionForm from '~/components/database-connection/DatabaseConnectionForm.vue'

const emit = defineEmits<{ changed: [] }>()
const route = useRoute()
const router = useRouter()
const api = useAPI()
const toast = useToast()
const confirm = useConfirmDialog()
const { t } = useI18n()
const { hasPermission } = usePermission()
const databaseConnectionId = route.params.databaseConnectionId as string

const { data: connection, error } = await useAsyncData(`database-connection-${databaseConnectionId}`, () =>
  api.manage.databaseConnections(databaseConnectionId).get(),
)
if (error.value) throw createError(error.value) // 404 / 403 はエラー画面にする

const formRef = ref<InstanceType<typeof DatabaseConnectionForm> | null>(null)
const close = () => router.push('/manage/database_connections')

const onDelete = async () => {
  const ok = await confirm({ title: t('DB接続を削除しますか？'), confirmLabel: t('削除'), color: 'error' })
  if (!ok) return
  try {
    await api.manage.databaseConnections(databaseConnectionId).delete()
    toast.add({ title: t('削除しました'), color: 'success' })
    emit('changed')
    close()
  } catch (err) {
    // Connectorが使っている接続は409で断られ、その理由を表示する
    toast.add({ title: t('エラー'), description: makeErrorMsg(err), color: 'error' })
  }
}
</script>

<template>
  <DDrawer :open="true" :title="$t('DB接続')" @update:open="(open) => !open && close()">
    <template #content>
      <div class="grid gap-6">
        <UAlert
          v-if="connection?.connectorCount"
          color="info"
          variant="subtle"
          icon="i-lucide-info"
          :description="
            $t('{count}件のConnectorが使っています。変更は、それらの次の操作から効きます', {
              count: connection.connectorCount,
            })
          "
        />
        <DatabaseConnectionForm ref="formRef" v-model="connection" @updated="emit('changed')" />
        <USeparator />
        <DatabaseConnectionCheckButton v-if="connection" :connection="connection" />
      </div>
    </template>
    <template #footer>
      <!-- 削除はサーバー側でも権限を確認し、権限がなければ403になる -->
      <DBtn
        v-if="hasPermission(PermissionCode.CHANGE_DATABASE_CONNECTION)"
        color="error"
        variant="ghost"
        @click="onDelete"
      >
        {{ $t('削除') }}
      </DBtn>
      <div class="flex-1" />
      <DBtn :disabled="!formRef?.canPost" :loading="formRef?.loading" @click="formRef?.onSubmit()">
        {{ $t('更新') }}
      </DBtn>
    </template>
  </DDrawer>
</template>
