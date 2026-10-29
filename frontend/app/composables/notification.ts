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

  // 要求の間にログインユーザーが変わったら、前の人への応答は反映しない
  const currentUser = () => useAccountStore().user?.id
  const stillSame = (user: string | undefined) => user === currentUser()

  const refreshCount = async () => {
    const user = currentUser()
    const count = await useAPI().notifications.unreadCount()
    if (stillSame(user)) unread.value = count.unread
  }

  const reload = async () => {
    const user = currentUser()
    loading.value = true
    try {
      const [page, count] = await Promise.all([
        useAPI().notifications.list({ pageSize: NOTIFICATION_PAGE_SIZE }),
        useAPI().notifications.unreadCount(),
      ])
      if (!stillSame(user)) return
      items.value = page.results
      unread.value = count.unread
    } finally {
      loading.value = false
    }
  }

  const markRead = async (notification: AppNotification) => {
    if (notification.read) return
    const user = currentUser()
    const updated = await useAPI().notifications.read(notification.id)
    if (!stillSame(user)) return
    items.value = items.value.map((n) => (n.id === updated.id ? updated : n))
    // 続けて押すと応答の前に同じ通知をもう一度送ることがあるので、数は減らさずにサーバーから読み直す
    await refreshCount()
  }

  const markAllRead = async () => {
    const user = currentUser()
    const count = await useAPI().notifications.readAll()
    if (!stillSame(user)) return
    unread.value = count.unread
    await reload()
  }

  return { unread, items, loading, refreshCount, reload, markRead, markAllRead }
})
