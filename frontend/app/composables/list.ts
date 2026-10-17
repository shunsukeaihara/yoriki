import type { Ref } from 'vue'

/** カーソル方式の一覧を前後のページに移動しながら読む */
export function useCursorList<T, TParams>(fetch: (params: TParams) => Promise<Paginated<T>>, params: Ref<TParams>) {
  const api = useAPI()
  const items = ref([]) as Ref<T[]>
  const next = ref<string | null>(null)
  const previous = ref<string | null>(null)
  const loading = ref(false)
  const error = ref<unknown>(null)

  // url を渡した場合は、DRF が返した next / previous の URL をそのまま取得する
  const load = async (url?: string | null) => {
    loading.value = true
    error.value = null
    try {
      const page = url ? await api.http.get<Paginated<T>, Paginated<T>>(url) : await fetch(params.value)
      items.value = page.results
      next.value = page.next
      previous.value = page.previous
    } catch (err) {
      error.value = err
    } finally {
      loading.value = false
    }
  }

  return {
    items,
    loading,
    error,
    hasNext: computed(() => Boolean(next.value)),
    hasPrevious: computed(() => Boolean(previous.value)),
    loadNext: () => load(next.value),
    loadPrevious: () => load(previous.value),
    reload: () => load(),
  }
}

/** 全ページを順に取得する。件数が少ないと分かっている一覧だけに使う */
export function useFetchAll<T>(fetch: () => Promise<Paginated<T>>) {
  const api = useAPI()
  const items = ref([]) as Ref<T[]>
  const loading = ref(false)
  const fetchAll = async () => {
    loading.value = true
    try {
      let page = await fetch()
      items.value = page.results
      while (page.next) {
        page = await api.http.get<Paginated<T>, Paginated<T>>(page.next)
        items.value = items.value.concat(page.results)
      }
    } finally {
      loading.value = false
    }
  }
  return { items, loading, fetchAll }
}
