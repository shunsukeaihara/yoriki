<script setup lang="ts">
/**
 * CSVの資料の検証の条件（必須列・キー列・列の型・文字コード・区切り）。公開の権限を持つ人だけが変えられる。
 * 条件を緩めて誤りを隠せないように、本文を編集できる人でも条件は変えられない（design.md §3.9）
 */
import DDialog from '../common/DDialog.vue'

const props = defineProps<{ skillId: string; draftId: string; revision: number; asset: SkillDraftAsset }>()
const emit = defineEmits<{ saved: [value: SkillDraftAsset]; failed: [error: unknown] }>()
const api = useAPI()
const toast = useToast()
const { t } = useI18n()
const dialog = ref<InstanceType<typeof DDialog> | null>(null)

const emptyForm = () => ({
  encoding: '' as CsvEncodingEnum | '',
  delimiter: '' as CsvDelimiterEnum | '',
  requiredColumns: [] as string[],
  keyColumns: [] as string[],
  types: {} as Record<string, CsvColumnTypeEnum>,
})
const form = ref(emptyForm())

// 選べる列は、今のファイルの見出しと、条件に既にある列
const columns = computed(() => {
  const info = (props.asset.validation?.info ?? {}) as { columns?: string[] }
  const checks = props.asset.checks
  return [
    ...new Set([
      ...(info.columns ?? []),
      ...(checks?.requiredColumns ?? []),
      ...(checks?.keyColumns ?? []),
      ...(checks?.columnTypes ?? []).map((c) => c.column),
    ]),
  ]
})
const columnItems = computed(() => columns.value.map((c) => ({ value: c, title: c })))

// 開くたびに今の条件を入れ直し、前回の入力を残さない
const onOpen = (open: boolean) => {
  if (!open) return
  const checks = props.asset.checks
  form.value = {
    encoding: (checks?.encoding ?? '') as CsvEncodingEnum | '',
    delimiter: (checks?.delimiter ?? '') as CsvDelimiterEnum | '',
    requiredColumns: [...(checks?.requiredColumns ?? [])],
    keyColumns: [...(checks?.keyColumns ?? [])],
    types: Object.fromEntries((checks?.columnTypes ?? []).map((c) => [c.column, c.type])),
  }
}

const { onSubmit, loading, errors } = createSubmit({
  form,
  fn: ({ encoding, delimiter, requiredColumns, keyColumns, types }) =>
    api
      .skills(props.skillId)
      .drafts(props.draftId)
      .assets(props.asset.key)
      .checks({
        revision: props.revision,
        checks: {
          encoding,
          delimiter,
          requiredColumns,
          keyColumns,
          columnTypes: Object.entries(types)
            .filter(([, type]) => type !== 'string')
            .map(([column, type]) => ({ column, type })),
        },
      }),
  onSuccess: (result) => {
    emit('saved', result)
    toast.add({ title: t('保存しました'), description: t('検証し直しました'), color: 'success' })
    dialog.value?.closeDialog()
  },
  onError: (err) => {
    emit('failed', err)
    toast.add({ title: t('エラー'), description: makeErrorMsg(err), color: 'error' })
  },
})
</script>

<template>
  <DDialog ref="dialog" :ui="{ content: 'sm:max-w-2xl' }" @update:open="onOpen">
    <template #trigger="{ on }">
      <slot name="trigger" :on="on" />
    </template>
    <template #header>{{ $t('資料「{key}」の検証の条件', { key: asset.key }) }}</template>
    <template #content>
      <DFormStack>
        <DFormAlert :errors="errors" />
        <div class="grid gap-4 sm:grid-cols-2">
          <DSelect v-model="form.encoding" :label="$t('文字コード')" :items="csvEncodingItems($t)" />
          <DSelect v-model="form.delimiter" :label="$t('区切り')" :items="csvDelimiterItems($t)" />
        </div>
        <DSelect
          v-model="form.keyColumns"
          :label="$t('キーの列')"
          :hint="$t('行を識別する列です。空・重複を誤りにし、差分を行ごとに示します')"
          :items="columnItems"
          multiple
          searchable
          clearable
        />
        <DSelect
          v-model="form.requiredColumns"
          :label="$t('必須の列')"
          :hint="$t('なければ誤りにします')"
          :items="columnItems"
          multiple
          searchable
          clearable
        />
        <DFormField
          :label="$t('列の型')"
          :hint="$t('型に合わない値を誤りにします。コード等の先頭の0は、文字列のままにしてください')"
        >
          <p v-if="!columns.length" class="text-sm text-muted">{{ $t('ファイルの見出しを読めませんでした') }}</p>
          <div class="grid gap-2 sm:grid-cols-2">
            <div v-for="column in columns" :key="column" class="flex items-center gap-2">
              <span class="min-w-0 flex-1 truncate font-mono text-sm" :title="column">{{ column }}</span>
              <DSelect
                :model-value="form.types[column] ?? 'string'"
                :label="column"
                :show-label="false"
                :items="csvColumnTypeItems($t)"
                class="w-32"
                @update:model-value="(value) => (form.types[column] = value as CsvColumnTypeEnum)"
              />
            </div>
          </div>
        </DFormField>
      </DFormStack>
    </template>
    <template #footer="{ closeDialog }">
      <DBtn color="neutral" variant="ghost" @click="closeDialog()">{{ $t('キャンセル') }}</DBtn>
      <DBtn :loading="loading" @click="onSubmit()">{{ $t('保存して検証') }}</DBtn>
    </template>
  </DDialog>
</template>
