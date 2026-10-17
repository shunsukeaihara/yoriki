<script setup lang="ts">
/**
 * API を検索して選ぶ。
 * - searchFn に ViewSet の list を渡す。入力した文字列は `search` パラメータで送る（DRF の SearchFilter）
 * - 一覧の末尾までスクロールすると、DRF が返した next の URL で続きを読む
 * - 編集画面では initialSelected に選択済みのオブジェクトを渡すと、表示名を復元できる
 */
type Item = Record<string, unknown>
type Value = string | number

const props = withDefaults(
  defineProps<{
    label?: string
    hint?: string
    description?: string
    errorMessages?: string[]
    required?: boolean
    showLabel?: boolean
    searchFn: (params: Record<string, unknown>) => Promise<Paginated<Item>>
    /** searchFn に毎回付けて送る検索条件 */
    queryParams?: Record<string, unknown>
    itemValue?: ItemAccessor<Item>
    itemTitle?: ItemAccessor<Item>
    itemDescription?: ItemAccessor<Item>
    multiple?: boolean
    initialSelected?: Item | Item[] | null
    clearable?: boolean
    placeholder?: string
    pageSize?: number
    class?: ClassProp
  }>(),
  {
    itemValue: 'id',
    itemTitle: 'name',
    multiple: false,
    initialSelected: null,
    clearable: true,
    pageSize: 20,
    showLabel: true,
    required: false,
  },
)
const modelValue = defineModel<Value | Value[] | null>()
const emit = defineEmits<{
  selected: [item: Item | null]
  unselected: [item: Item]
}>()
defineOptions({ inheritAttrs: false })

const api = useAPI()
const menu = useTemplateRef('menu')
const searchTerm = ref('')
const results = ref<Item[]>([])
const nextUrl = ref<string | null>(null)
const loading = ref(false)
// 検索結果に含まれない選択済みの項目も表示名を出せるように保持する
const selectedItems = ref<Item[]>([])

const valueOf = (item: Item) => getProp(item, props.itemValue) as Value
const toOption = (item: Item) => ({
  value: valueOf(item),
  label: String(getProp(item, props.itemTitle) ?? ''),
  description: props.itemDescription ? String(getProp(item, props.itemDescription) ?? '') : undefined,
  raw: item,
})

const options = computed(() => {
  const resultValues = new Set(results.value.map(valueOf))
  const missing = selectedItems.value.filter((i) => !resultValues.has(valueOf(i)))
  return [...missing, ...results.value].map(toOption)
})

let requestId = 0
const search = async () => {
  const current = ++requestId
  loading.value = true
  try {
    const page = await props.searchFn({
      ...props.queryParams,
      search: searchTerm.value || undefined,
      pageSize: props.pageSize,
    })
    if (current !== requestId) return
    results.value = page.results
    nextUrl.value = page.next
  } finally {
    if (current === requestId) loading.value = false
  }
}

const loadMore = async () => {
  if (!nextUrl.value || loading.value) return
  loading.value = true
  try {
    const page = await api.http.get<Paginated<Item>, Paginated<Item>>(nextUrl.value)
    results.value = results.value.concat(page.results)
    nextUrl.value = page.next
  } finally {
    loading.value = false
  }
}

watchDebounced(searchTerm, search, { debounce: 300 })

const onOpen = (open: boolean) => {
  if (open && results.value.length === 0) search()
}

onMounted(() => {
  useInfiniteScroll(() => menu.value?.viewportRef, loadMore, {
    distance: 10,
    canLoadMore: () => Boolean(nextUrl.value),
  })
})

const setItem = (item: Item) => {
  const value = valueOf(item)
  if (!selectedItems.value.some((i) => valueOf(i) === value)) selectedItems.value.push(item)
  if (props.multiple) {
    const current = Array.isArray(modelValue.value) ? modelValue.value : []
    if (!current.includes(value)) modelValue.value = [...current, value]
  } else {
    selectedItems.value = [item]
    modelValue.value = value
  }
}

const clear = () => {
  selectedItems.value = []
  modelValue.value = props.multiple ? [] : null
}

const onUpdate = (value: unknown) => {
  const findRaw = (v: Value) => options.value.find((o) => o.value === v)?.raw
  if (props.multiple) {
    const next = (Array.isArray(value) ? value : []) as Value[]
    const removed = selectedItems.value.filter((i) => !next.includes(valueOf(i)))
    removed.forEach((i) => emit('unselected', i))
    selectedItems.value = next.map((v) => findRaw(v)).filter((i): i is Item => Boolean(i))
    modelValue.value = next
    return
  }
  const raw = value === null || value === undefined ? null : (findRaw(value as Value) ?? null)
  selectedItems.value = raw ? [raw] : []
  modelValue.value = (value ?? null) as Value | null
  emit('selected', raw)
}

watch(
  () => props.initialSelected,
  (initial) => {
    if (!initial) return
    // 初期選択は値も設定する（編集画面では既存の値と同じになる）
    const items = Array.isArray(initial) ? initial : [initial]
    items.forEach(setItem)
  },
  { immediate: true },
)

defineExpose({ clear, setItem, reload: search })
</script>

<template>
  <DFormField v-bind="pickFieldProps(props)">
    <USelectMenu
      ref="menu"
      v-model:search-term="searchTerm"
      :model-value="(modelValue ?? undefined) as any"
      :items="options"
      value-key="value"
      :multiple="multiple"
      :clear="clearable"
      :loading="loading"
      ignore-filter
      :search-input="{ placeholder: $t('検索') }"
      :placeholder="placeholder ?? $t('選択してください')"
      class="w-full"
      v-bind="$attrs"
      @update:open="onOpen"
      @update:model-value="onUpdate"
    >
      <template v-if="$slots.option" #item="{ item }">
        <slot name="option" :item="(item as (typeof options)[number]).raw" />
      </template>
      <template v-if="$slots.footer" #content-bottom>
        <slot name="footer" />
      </template>
    </USelectMenu>
  </DFormField>
</template>
