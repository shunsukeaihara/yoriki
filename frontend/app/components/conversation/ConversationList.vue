<script setup lang="ts">
// サイドバーの会話の一覧。Run の状態が変わったら、ログインユーザー宛ての通知で読み直す
defineProps<{ collapsed?: boolean }>()
const store = useConversationListStore()
const route = useRoute()
const toast = useToast()
const { t } = useI18n()

onMounted(async () => {
  try {
    await store.reload()
  } catch (err) {
    toast.add({ title: t('会話の一覧を読めませんでした'), description: makeErrorMsg(err), color: 'error' })
  }
})
useUserEvents((event) => store.refreshOne(event.conversation), {
  // 通知は保存しないので、切れていた間に変わった分は一覧ごと読み直す
  onReconnect: () => store.reload().catch(() => {}),
})

const currentId = computed(() => route.params.conversationId as string | undefined)
</script>

<template>
  <div v-if="!collapsed" class="grid gap-0.5">
    <NuxtLink
      v-for="conversation in store.items"
      :key="conversation.id"
      :to="`/conversations/${conversation.id}`"
      :class="[
        'flex items-center gap-2 rounded-md px-2 py-1.5 text-sm hover:bg-elevated',
        conversation.id === currentId ? 'bg-elevated font-medium text-highlighted' : 'text-default',
      ]"
    >
      <span class="flex-1 truncate">{{ conversation.title || $t('新しい会話') }}</span>
      <UIcon
        v-if="conversation.handlesSensitiveData"
        name="i-lucide-shield"
        class="size-3.5 shrink-0 text-muted"
        :aria-label="$t('個人情報・機密情報を扱う')"
      />
      <UIcon
        v-if="conversation.activeRun?.status === RunStatus.WAITING_USER"
        name="i-lucide-message-circle-question"
        class="size-4 shrink-0 text-warning"
        :aria-label="conversation.activeRun.statusDisplay"
      />
      <UIcon
        v-else-if="conversation.activeRun"
        name="i-lucide-loader-circle"
        class="size-4 shrink-0 animate-spin text-primary"
        :aria-label="conversation.activeRun.statusDisplay"
      />
    </NuxtLink>
    <DBtn
      v-if="store.hasMore"
      variant="link"
      color="neutral"
      :loading="store.loading"
      class="justify-start"
      @click="store.loadMore()"
    >
      {{ $t('さらに表示') }}
    </DBtn>
  </div>
</template>
