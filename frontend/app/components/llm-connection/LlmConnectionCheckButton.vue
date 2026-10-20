<script setup lang="ts">
/** 接続の確認。接続先に問い合わせ、API key と接続先が正しいかと、使えるモデル名を表示する */
const props = defineProps<{ connectionId: string }>()
const api = useAPI()
const toast = useToast()
const { t } = useI18n()
const loading = ref(false)
const result = ref<LLMConnectionCheck | null>(null)

const onCheck = async () => {
  loading.value = true
  result.value = null
  try {
    result.value = await api.manage.llmConnections(props.connectionId).check()
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
      :description="$t('使えるモデル：{count}件', { count: result.models.length })"
    />
    <UAlert
      v-else-if="result"
      color="error"
      variant="subtle"
      icon="i-lucide-circle-x"
      :title="$t('接続できませんでした')"
      :description="result.detail"
    />
    <div v-if="result?.ok && result.models.length" class="flex max-h-48 flex-wrap gap-1 overflow-y-auto">
      <UBadge v-for="name in result.models" :key="name" color="neutral" variant="subtle">{{ name }}</UBadge>
    </div>
  </div>
</template>
