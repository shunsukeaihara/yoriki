// 画面の通知（通知欄）。未読の数はタブに1つだけ持ち、通知が届いたらログインユーザー宛てのストリームで知って読み直す

// 通知欄に出す件数（古いものは出さない。一覧の画面はタスクの Run の画面とあわせて作る）
export const NOTIFICATION_PAGE_SIZE = 20

export const useNotificationStore = defineStore('notification', () => {
  const unread = ref(0)
  const items = ref<AppNotification[]>([])
  const loading = ref(false)

  // ログアウト・別の人のログインでは、前の人の通知を残さない（Run を見られる人は利用者ごと。authorization.md §7.1）
  watch(
    () => useAccountStore().user?.id,
    () => {
      items.value = []
      unread.value = 0
    },
  )

  const refreshCount = async () => {
    unread.value = (await useAPI().notifications.unreadCount()).unread
  }

  const reload = async () => {
    loading.value = true
    try {
      const [page, count] = await Promise.all([
        useAPI().notifications.list({ pageSize: NOTIFICATION_PAGE_SIZE }),
        useAPI().notifications.unreadCount(),
      ])
      items.value = page.results
      unread.value = count.unread
    } finally {
      loading.value = false
    }
  }

  const markRead = async (notification: AppNotification) => {
    if (notification.read) return
    const updated = await useAPI().notifications.read(notification.id)
    items.value = items.value.map((n) => (n.id === updated.id ? updated : n))
    // 続けて押すと応答の前に同じ通知をもう一度送ることがあるので、数は減らさずにサーバーから読み直す
    await refreshCount()
  }

  const markAllRead = async () => {
    unread.value = (await useAPI().notifications.readAll()).unread
    await reload()
  }

  return { unread, items, loading, refreshCount, reload, markRead, markAllRead }
})
