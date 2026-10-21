<script setup lang="ts">
const api = useAPI()
const toast = useToast()
const { t } = useI18n()
const listStore = useConversationListStore()

const { data: models, error } = await useAsyncData('app-model-profiles', () => api.modelProfiles.list(), {
  default: () => [],
})
if (error.value) throw createError(error.value)

const onStarted = async (conversation: Conversation, _message: Message | null, err: unknown) => {
  listStore.upsert(
    await api
      .conversations(conversation.id)
      .get()
      .catch(() => conversation),
  )
  if (err) toast.add({ title: t('依頼を送れませんでした'), description: makeErrorMsg(err), color: 'error' })
  await navigateTo(`/conversations/${conversation.id}`)
}
</script>

<template>
  <UDashboardPanel id="conversation-start">
    <template #header>
      <UDashboardNavbar :title="$t('新しい会話')" />
    </template>
    <template #body>
      <UContainer class="max-w-3xl py-8">
        <UAlert
          v-if="!models.length"
          color="warning"
          variant="subtle"
          icon="i-lucide-triangle-alert"
          :title="$t('使えるモデルがありません。基盤管理者に問い合わせてください。')"
        />
        <ConversationStartForm v-else :models="models" @started="onStarted" />
      </UContainer>
    </template>
  </UDashboardPanel>
</template>
