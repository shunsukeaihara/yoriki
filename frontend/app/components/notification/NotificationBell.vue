<script setup lang="ts">
// サイドバーの通知のボタン。未読の数を出し、開くと最近の通知を一覧にする。開いた通知は既読にする
defineProps<{ collapsed?: boolean }>()
const store = useNotificationStore()
const toast = useToast()
const { t } = useI18n()
const open = ref(false)
const unreadLabel = computed(() => (store.unread > 99 ? '99+' : String(store.unread)))

const notify = (err: unknown) =>
  toast.add({ title: t('通知を読めませんでした'), description: makeErrorMsg(err), color: 'error' })

onMounted(() => store.refreshCount().catch(() => {}))
useUserEvents(
  (event) => {
    if (event.type !== 'notification') return
    ;(open.value ? store.reload() : store.refreshCount()).catch(() => {})
  },
  // 通知は保存しないので、切れていた間に届いた分は数え直す
  { onReconnect: () => store.refreshCount().catch(() => {}) },
)

watch(open, (value) => {
  if (value) store.reload().catch(notify)
})

const icons: Record<NotifyEventEnum, string> = {
  'run.completed': 'i-lucide-circle-check',
  'run.failed': 'i-lucide-circle-x',
  'run.cancelled': 'i-lucide-circle-slash',
}
const colors: Record<NotifyEventEnum, string> = {
  'run.completed': 'text-success',
  'run.failed': 'text-error',
  'run.cancelled': 'text-muted',
}
</script>

<template>
  <UPopover v-model:open="open" :content="{ side: 'right', align: 'start' }">
    <UChip :show="collapsed && store.unread > 0" size="3xl" inset :class="collapsed ? undefined : 'w-full'">
      <DBtn
        icon="i-lucide-bell"
        variant="ghost"
        color="neutral"
        :label="collapsed ? undefined : $t('通知')"
        :aria-label="store.unread > 0 ? $t('通知（未読{count}件）', { count: store.unread }) : $t('通知')"
        :block="!collapsed"
        :class="collapsed ? undefined : 'justify-start'"
      >
        <template v-if="!collapsed && store.unread > 0" #trailing>
          <UBadge :label="unreadLabel" size="sm" class="ms-auto" />
        </template>
      </DBtn>
    </UChip>

    <template #content>
      <div class="flex w-80 max-w-[calc(100vw-2rem)] flex-col">
        <div class="flex items-center justify-between border-b border-default px-3 py-2">
          <span class="text-sm font-medium">{{ $t('通知') }}</span>
          <DBtn
            v-if="store.unread > 0"
            size="xs"
            variant="link"
            color="neutral"
            :label="$t('すべて既読にする')"
            @click="store.markAllRead().catch(notify)"
          />
        </div>
        <div class="max-h-96 overflow-y-auto">
          <p v-if="!store.loading && store.items.length === 0" class="px-3 py-6 text-center text-sm text-muted">
            {{ $t('通知はありません') }}
          </p>
          <button
            v-for="item in store.items"
            :key="item.id"
            type="button"
            class="flex w-full items-start gap-2 px-3 py-2 text-left hover:bg-elevated"
            @click="store.markRead(item).catch(notify)"
          >
            <UIcon :name="icons[item.eventType]" :class="['mt-0.5 size-4 shrink-0', colors[item.eventType]]" />
            <span class="flex min-w-0 flex-1 flex-col">
              <span :class="['truncate text-sm', item.read ? 'text-muted' : 'font-medium text-highlighted']">
                {{ item.taskName }}
              </span>
              <span class="text-xs text-muted">
                {{ $t('{event}・{time}', { event: item.eventDisplay, time: formatDatetime(item.created) }) }}
              </span>
            </span>
            <span v-if="!item.read" class="mt-1.5 size-2 shrink-0 rounded-full bg-primary" :aria-label="$t('未読')" />
          </button>
        </div>
      </div>
    </template>
  </UPopover>
</template>
