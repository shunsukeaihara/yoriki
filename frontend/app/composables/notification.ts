// 画面の通知（通知欄）。未読の数はタブに1つだけ持ち、通知が届いたらログインユーザー宛てのストリームで知って読み直す

// 通知欄に出す件数（古いものは出さない。一覧の画面はタスクの Run の画面とあわせて作る）
export const NOTIFICATION_PAGE_SIZE = 20

export const useNotificationStore = defineStore('notification', () => {
  const unread = ref(0)
  const items = ref<AppNotification[]>([])
  const loading = ref(false)

  // 応答は、最後に始めた要求のものだけを反映する（要求が重なったとき、遅れて届いた古い一覧・数で巻き戻さない）。
  // 利用者が変わったときも世代を進め、前の人への応答を捨てる
  let listGeneration = 0
  let countGeneration = 0
  // 利用者が変わった回数。既読の操作は互いに打ち消さず、利用者が変わったときだけ応答を捨てる
  let userGeneration = 0

  // ログアウト・別の人のログインでは、前の人の通知を残さない（Run を見られる人は利用者ごと。authorization.md §7.1）
  watch(
    () => useAccountStore().user?.id,
    () => {
      userGeneration++
      listGeneration++
      countGeneration++
      items.value = []
      unread.value = 0
    },
  )

  const refreshCount = async () => {
    const generation = ++countGeneration
    const count = await useAPI().notifications.unreadCount()
    if (generation === countGeneration) unread.value = count.unread
  }

  const reload = async () => {
    const list = ++listGeneration
    const counted = ++countGeneration
    loading.value = true
    try {
      const [page, count] = await Promise.all([
        useAPI().notifications.list({ pageSize: NOTIFICATION_PAGE_SIZE }),
        useAPI().notifications.unreadCount(),
      ])
      if (list === listGeneration) items.value = page.results
      if (counted === countGeneration) unread.value = count.unread
    } finally {
      loading.value = false
    }
  }

  const markRead = async (notification: AppNotification) => {
    if (notification.read) return
    const user = userGeneration
    const updated = await useAPI().notifications.read(notification.id)
    if (user !== userGeneration) return
    // 先に始めた取得が遅れて届いても、既読にした通知を未読に戻さない
    listGeneration++
    items.value = items.value.map((n) => (n.id === updated.id ? updated : n))
    // 続けて押すと応答の前に同じ通知をもう一度送ることがあるので、数は減らさずにサーバーから読み直す
    await refreshCount()
  }

  const markAllRead = async () => {
    const user = userGeneration
    await useAPI().notifications.readAll()
    if (user !== userGeneration) return
    await reload()
  }

  return { unread, items, loading, refreshCount, reload, markRead, markAllRead }
})
