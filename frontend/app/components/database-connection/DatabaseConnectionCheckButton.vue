<script setup lang="ts">
/** 接続の確認。接続先に接続して、ホスト・認証・DB名が正しいかを表示する */
const props = defineProps<{ connection: DatabaseConnection }>()
const api = useAPI()
const toast = useToast()
const { t } = useI18n()
const loading = ref(false)
const result = ref<ConnectorCheck | null>(null)

const onCheck = async () => {
  loading.value = true
  result.value = null
  try {
    result.value = await api.manage
      .databaseConnections(props.connection.id)
      .check(connectionWaitSeconds(props.connection))
  } catch (err) {
    toast.add({ title: t('エラー'), description: makeErrorMsg(err), color: 'error' })
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <div class="grid gap-3">
    <div>
      <DBtn color="neutral" variant="outline" icon="i-lucide-activity" :loading="loading" @click="onCheck">
        {{ $t('接続を確認') }}
      </DBtn>
    </div>
    <UAlert
      v-if="result?.ok"
      color="success"
      variant="subtle"
      icon="i-lucide-circle-check"
      :title="$t('接続できました')"
    />
    <UAlert
      v-else-if="result"
      color="error"
      variant="subtle"
      icon="i-lucide-circle-x"
      :title="$t('接続できませんでした')"
      :description="result.detail"
    />
  </div>
</template>
