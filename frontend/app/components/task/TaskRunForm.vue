<script setup lang="ts">
import { AxiosError } from 'axios'

/**
 * タスクの手動実行とチャットからの起動（authorization.md §6）。タスクの今の版で、決めた入力の項目の値だけを渡す（自由な
 * 依頼文は受けない）。手動実行では部門・全社・システムのタスクは起動した人ではなくタスクの範囲で動き、チャットからの
 * 起動（`conversationId` を渡す）では起動した人の権限で動く。型と必須はサーバーが確かめ、項目ごとに返す
 */
const props = defineProps<{
  task: TaskDetail | LaunchableTask
  /** チャットから起動するときの、起動した会話（カードが足される） */
  conversationId?: string
}>()
const api = useAPI()
const toast = useToast()
const { t } = useI18n()
const emit = defineEmits<{
  started: [value: Run | TaskRun]
  failed: [error: unknown]
  /** 画面を開いた後に版が変わった（409）。最新の状態を読み込み直す */
  conflicted: []
  /** 応答を受け取れなかった（受け付けたかが分からない）。実行の履歴で確かめる */
  unknown: []
}>()

// 入力の項目はタスクの版で決まる。版が変わったら、親がこのフォームを作り直す（key）
const inputs = props.task.inputs ?? []
// 必須の真偽はチェックボックス（初期値はいいえ）。任意の真偽は「未指定」も選べるようにし、未指定なら送らない
const emptyValue = (input: TaskInput): TaskRunInputs[string] =>
  input.type === TaskInputType.BOOLEAN && input.required ? false : null
const booleanItems = computed(() => [
  { value: true, title: t('はい') },
  { value: false, title: t('いいえ') },
])
const form = ref<TaskRunInputs>(Object.fromEntries(inputs.map((i) => [i.name, emptyValue(i)])))
const choiceItems = (input: TaskInput) => (input.choices ?? []).map((value) => ({ value, title: value }))
// 入力欄ごとに v-model の型を合わせる（値は form にまとめて持つ）
const modelOf = <V,>(name: string) =>
  computed({
    get: () => (form.value[name] ?? null) as V | null,
    set: (value: V | null) => (form.value[name] = value as TaskRunInputs[string]),
  })
const fields = inputs.map((input) => ({
  input,
  text: modelOf<string>(input.name),
  number: modelOf<number>(input.name),
  flag: computed({
    get: () => Boolean(form.value[input.name]),
    set: (value: boolean) => (form.value[input.name] = value),
  }),
  file: modelOf<File>(input.name),
  choice: modelOf<boolean>(input.name),
}))

// チャットからの起動のID。フォームを作るたびに作り、同じフォームからの再送では同じIDを送る（二重に起動しない）
const clientRequestId = crypto.randomUUID()
const { onSubmit, loading, errors } = createSubmit({
  form,
  // 画面で確かめた版を送る。別の版ができていれば409になり、確かめていない定義では実行しない
  fn: (data): Promise<Run | TaskRun> =>
    props.conversationId
      ? api.launchableTasks(props.task.id).run(props.conversationId, props.task.version ?? 0, clientRequestId, data)
      : api.tasks(props.task.id).run(props.task.version ?? 0, data),
  onSuccess: (run) => {
    emit('started', run)
    toast.add({ title: props.conversationId ? t('タスクを起動しました') : t('実行を始めました'), color: 'success' })
  },
  onError: (err) => {
    emit('failed', err)
    rawInputErrors.value = err instanceof AxiosError ? taskInputErrors(err) : undefined
    if (err instanceof AxiosError && err.response?.status === 409) {
      emit('conflicted')
      toast.add({ title: t('エラー'), description: makeErrorMsg(err), color: 'error' })
    } else if (err instanceof AxiosError && !err.response) {
      // 送った後に応答だけが失われたときは、受け付けている。再送で二重に実行しないよう、先に履歴を見せる
      emit('unknown')
      toast.add({
        title: t('受け付けたかを確かめられませんでした'),
        description: props.conversationId
          ? t(
              '会話にタスクのカードが出ていれば受け付けています。出ていなければ、もう一度起動してください（同じ起動は二重になりません）',
            )
          : t('実行の履歴に出ていれば受け付けています。出ていなければ、もう一度実行してください'),
        color: 'warning',
      })
    } else {
      toast.add({ title: t('エラー'), description: makeErrorMsg(err), color: 'error' })
    }
  },
})
// 入力の項目の誤り（{ inputs: { 項目の名前: [...] } }）と、入力全体の誤り（文の並び）。キーは項目の名前なので、
// camelCase に変換する前の応答から引く（foo_bar と foo__bar のように、変換すると同じになる名前があるため）
const rawInputErrors = ref<Record<string, unknown> | string[] | undefined>()
const errorsOf = (name: string) =>
  rawInputErrors.value && !Array.isArray(rawInputErrors.value) ? inputErrorsOf(rawInputErrors.value, name) : undefined
const generalErrors = computed(() => ({
  nonFieldErrors: [
    ...(errors.value.nonFieldErrors ?? []),
    ...(Array.isArray(rawInputErrors.value) ? rawInputErrors.value.map(String) : []),
  ],
}))

// 起動の候補は起動できるタスクだけなので、チャットからの起動ではいつも送れる（断る理由はサーバーが返す）
const canPost = computed(() => ('canRun' in props.task ? Boolean(props.task.canRun) : true))

defineExpose({ onSubmit, loading, canPost })
</script>

<template>
  <DFormStack>
    <DFormAlert :errors="generalErrors" />
    <p v-if="!fields.length" class="text-sm text-muted">{{ $t('このタスクには入力の項目がありません') }}</p>
    <template v-for="{ input, text, number, flag, file, choice } in fields" :key="input.name">
      <DSelect
        v-if="input.type === TaskInputType.BOOLEAN && !input.required"
        v-model="choice.value"
        :label="input.label"
        :hint="input.description"
        :items="booleanItems"
        :placeholder="$t('未指定')"
        clearable
        :error-messages="errorsOf(input.name)"
      />
      <DCheckbox
        v-else-if="input.type === TaskInputType.BOOLEAN"
        v-model="flag.value"
        :label="input.label"
        :hint="input.description"
        :error-messages="errorsOf(input.name)"
      />
      <DNumberField
        v-else-if="input.type === TaskInputType.NUMBER || input.type === TaskInputType.INTEGER"
        v-model="number.value"
        :label="input.label"
        :hint="input.description"
        :required="input.required"
        :step="input.type === TaskInputType.INTEGER ? 1 : undefined"
        :error-messages="errorsOf(input.name)"
      />
      <DDatePicker
        v-else-if="input.type === TaskInputType.DATE"
        v-model="text.value"
        :label="input.label"
        :hint="input.description"
        :required="input.required"
        :error-messages="errorsOf(input.name)"
      />
      <DSelect
        v-else-if="input.type === TaskInputType.CHOICE"
        v-model="text.value"
        :label="input.label"
        :hint="input.description"
        :required="input.required"
        :items="choiceItems(input)"
        :clearable="!input.required"
        :error-messages="errorsOf(input.name)"
      />
      <DFileInputField
        v-else-if="input.type === TaskInputType.FILE"
        v-model="file.value"
        :label="input.label"
        :hint="input.description"
        :required="input.required"
        :error-messages="errorsOf(input.name)"
      />
      <DTextarea
        v-else-if="!input.maxLength || input.maxLength > 200"
        v-model="text.value"
        :label="input.label"
        :hint="input.description"
        :required="input.required"
        :rows="2"
        :maxlength="input.maxLength ?? undefined"
        :error-messages="errorsOf(input.name)"
        class="max-w-none"
      />
      <DTextField
        v-else
        v-model="text.value"
        :label="input.label"
        :hint="input.description"
        :required="input.required"
        :maxlength="input.maxLength"
        :error-messages="errorsOf(input.name)"
      />
    </template>
  </DFormStack>
</template>
