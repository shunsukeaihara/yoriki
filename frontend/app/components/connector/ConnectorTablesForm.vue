<script setup lang="ts">
/**
 * 公開する表・ビューと説明の編集。エージェントは、ここで公開した表・ビューだけを問い合わせられる（design.md §4.9.2）。
 * 接続先のアカウントが読める表・ビューの一覧から選び、表と列に説明を付ける。保存すると新しい版になる。
 * 個人情報を含む列は、その列を除いたビューをDBに作って公開する
 */
const api = useAPI()
const toast = useToast()
const { t } = useI18n()
const modelValue = defineModel<Connector | null>()
const emit = defineEmits<{
  updated: [value: Connector]
  failed: [error: unknown]
}>()

type Entry = { schema: string; name: string; description: string; columns: Record<string, string> }

const keyOf = (table: { schema: string; name: string }) => `${table.schema}\u0000${table.name}`
const toEntries = (tables: ConnectorTableEntry[] | undefined): Entry[] =>
  (tables ?? []).map((e) => ({
    schema: e.schema,
    name: e.name,
    description: e.description,
    columns: { ...(e.columns ?? {}) },
  }))

const toForm = (value: Connector | null | undefined) => ({
  version: value?.version ?? 0,
  tables: toEntries(value?.tables),
  changeReason: '',
})
const form = ref(toForm(null))
const { reset } = syncConnectorForm(modelValue, form, toForm)

// 接続先の表・ビューと列（読み込むまでは null）
const dbTables = ref<ConnectorTable[] | null>(null)
const dbError = ref('')
const loadingDb = ref(false)
const dbTableMap = computed(() => new Map((dbTables.value ?? []).map((table) => [keyOf(table), table])))

const loadDbTables = async () => {
  const connector = modelValue.value
  if (!connector) return
  loadingDb.value = true
  dbError.value = ''
  try {
    const result = await api.manage.connectors(connector.id).tables(connectionWaitSeconds(connector.connectionInfo))
    dbTables.value = result.ok ? result.tables : null
    dbError.value = result.detail
  } catch (err) {
    dbError.value = makeErrorMsg(err)
  } finally {
    loadingDb.value = false
  }
}
/** タブを初めて開いたときに、接続先の一覧を読む */
const loadOnce = () => {
  if (dbTables.value === null && !loadingDb.value && !dbError.value) loadDbTables()
}

// 選択肢は接続先の表・ビュー。公開中で接続先に見つからないものも、外せるように残す
const tableItems = computed(() => {
  const items = (dbTables.value ?? []).map((table) => ({
    value: keyOf(table),
    title: tableLabel(table),
    description: table.kind === 'view' ? t('ビュー') : t('表'),
  }))
  for (const entry of form.value.tables) {
    if (!dbTableMap.value.has(keyOf(entry)))
      items.push({ value: keyOf(entry), title: tableLabel(entry), description: t('接続先に見つかりません') })
  }
  return items
})
const selectedKeys = computed({
  get: () => form.value.tables.map(keyOf),
  set: (keys: string[]) => {
    const current = new Map(form.value.tables.map((entry) => [keyOf(entry), entry]))
    form.value.tables = keys.map((key) => {
      const existing = current.get(key)
      if (existing) return existing
      const table = dbTableMap.value.get(key)!
      return { schema: table.schema, name: table.name, description: table.description, columns: {} }
    })
  },
})
const removeTable = (index: number) => form.value.tables.splice(index, 1)

/** 列の説明の入力欄。接続先の列（型付き）と、説明を付けた列（接続先に見つからないものも）を並べる */
const columnsOf = (entry: Entry) => {
  const columns = (dbTableMap.value.get(keyOf(entry))?.columns ?? []).map((c) => ({ name: c.name, type: c.type }))
  const known = new Set(columns.map((c) => c.name))
  for (const name of Object.keys(entry.columns)) if (!known.has(name)) columns.push({ name, type: '' })
  return columns
}
const setColumnDescription = (entry: Entry, column: string, value: string | null | undefined) => {
  // 既にある列は同じ位置のまま書き換える（版の中身の順序を保つ）
  entry.columns = value
    ? { ...entry.columns, [column]: value }
    : Object.fromEntries(Object.entries(entry.columns).filter(([name]) => name !== column))
}

// 空の説明は送らない（版の中身を、説明を付けた列だけにそろえる）
const normalized = (tables: Entry[]) =>
  tables.map((entry) => ({
    schema: entry.schema,
    name: entry.name,
    description: entry.description.trim(),
    columns: Object.fromEntries(
      Object.entries(entry.columns)
        .map(([column, description]) => [column, description.trim()])
        .filter(([, description]) => description),
    ),
  }))
const dirty = computed(
  () =>
    JSON.stringify(normalized(form.value.tables)) !== JSON.stringify(normalized(toEntries(modelValue.value?.tables))),
)

const { onSubmit, loading, errors } = createSubmit({
  form,
  fn: ({ version, tables, changeReason }) =>
    api.manage.connectors(modelValue.value!.id).patch({
      version,
      tables: normalized(tables),
      changeReason: changeReason || undefined,
    }),
  onSuccess: (result) => {
    emit('updated', result)
    modelValue.value = result
    reset()
    toast.add({ title: t('保存しました'), color: 'success' })
  },
  onError: (err) => {
    emit('failed', err)
    toast.add({ title: t('エラー'), description: makeErrorMsg(err), color: 'error' })
  },
})
// 表ごとのエラー（{ "0": { name: [...] } }）は、表の名前を付けて先頭に出す
const tableErrors = computed(() =>
  flattenRowErrors(errors.value.tables, (i, message) => {
    const entry = form.value.tables[i]
    return entry ? `${tableLabel(entry)}: ${message}` : message
  }),
)

const canPost = computed(() => Boolean(modelValue.value) && dirty.value)

defineExpose({ onSubmit, loading, canPost, loadOnce, reset })
</script>

<template>
  <DFormStack>
    <DFormAlert :errors="{ nonFieldErrors: [...(errors.nonFieldErrors ?? []), ...tableErrors] }" />
    <UAlert
      color="info"
      variant="subtle"
      icon="i-lucide-shield-check"
      :description="
        $t(
          'エージェントは、公開した表・ビューだけを読み取れます。個人情報を含む列は、その列を除いたビューをDBに作って公開してください',
        )
      "
    />

    <div class="grid gap-2">
      <div class="flex items-end gap-2">
        <DSelect
          v-model="selectedKeys"
          :label="$t('公開する表・ビュー')"
          :items="tableItems"
          item-description="description"
          multiple
          searchable
          :disabled="dbTables === null"
          :placeholder="dbTables === null ? $t('接続先の一覧を読み込むと選べます') : $t('選択してください')"
          class="flex-1"
        />
        <DBtn color="neutral" variant="outline" icon="i-lucide-refresh-cw" :loading="loadingDb" @click="loadDbTables">
          {{ dbTables === null ? $t('接続先の一覧を読み込む') : $t('読み込み直す') }}
        </DBtn>
      </div>
      <UAlert
        v-if="dbError"
        color="error"
        variant="subtle"
        icon="i-lucide-circle-x"
        :title="$t('接続先の表・ビューを読み込めませんでした')"
        :description="dbError"
      />
    </div>

    <p v-if="!form.tables.length" class="text-sm text-muted">{{ $t('公開している表・ビューはありません') }}</p>
    <UCard v-for="(entry, index) in form.tables" :key="keyOf(entry)" :ui="{ body: 'p-3 sm:p-3' }">
      <div class="grid gap-3">
        <div class="flex items-center gap-2">
          <span class="font-mono text-sm font-semibold">{{ tableLabel(entry) }}</span>
          <UBadge v-if="dbTableMap.get(keyOf(entry))?.kind === 'view'" color="neutral" variant="subtle" size="sm">
            {{ $t('ビュー') }}
          </UBadge>
          <UBadge v-else-if="dbTables && !dbTableMap.has(keyOf(entry))" color="warning" variant="subtle" size="sm">
            {{ $t('接続先に見つかりません') }}
          </UBadge>
          <div class="flex-1" />
          <DBtn
            color="neutral"
            variant="ghost"
            size="xs"
            icon="i-lucide-x"
            :aria-label="$t('公開をやめる')"
            @click="removeTable(index)"
          />
        </div>
        <DTextField v-model="entry.description" :label="$t('表の説明')" :hint="$t('エージェントに見せます')" />
        <UCollapsible v-if="columnsOf(entry).length">
          <DBtn color="neutral" variant="link" size="xs" icon="i-lucide-chevron-down" class="px-0">
            {{
              $t('列の説明（{count}列のうち{described}列）', {
                count: columnsOf(entry).length,
                described: Object.keys(entry.columns).length,
              })
            }}
          </DBtn>
          <template #content>
            <div class="mt-2 grid grid-cols-[minmax(6rem,auto)_1fr] items-center gap-x-3 gap-y-2">
              <template v-for="column in columnsOf(entry)" :key="column.name">
                <div class="min-w-0">
                  <div class="truncate font-mono text-xs">{{ column.name }}</div>
                  <div class="truncate text-xs text-muted">{{ column.type }}</div>
                </div>
                <DTextField
                  :model-value="entry.columns[column.name] ?? ''"
                  :aria-label="$t('{column}の説明', { column: column.name })"
                  size="sm"
                  @update:model-value="(value) => setColumnDescription(entry, column.name, value)"
                />
              </template>
            </div>
          </template>
        </UCollapsible>
      </div>
    </UCard>

    <DTextarea
      v-model="form.changeReason"
      :label="$t('変更理由')"
      :hint="$t('版の履歴に残ります')"
      :error-messages="errors.changeReason"
    />
  </DFormStack>
</template>
