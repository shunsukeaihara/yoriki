<script setup lang="ts">
/**
 * 入口（Webhook）の受け取り方の編集（design.md §4.2）。イベントIDの場所・項目の取り出し・payloadの上限を変えて保存すると
 * 新しい版になる。名前は版の外。項目の取り出しは、タスクの今の版の入力の項目の名前と、payloadの中の場所（JSONのpath）。
 * 型と必須かはタスクの入力の項目で決まる。編集中の受け取り方は settings で公開し、サンプルのpayloadで試すのに使う
 */
const api = useAPI()
const toast = useToast()
const { t } = useI18n()
const modelValue = defineModel<TriggerBinding | null>()
const emit = defineEmits<{ updated: [value: TriggerBinding]; failed: [error: unknown] }>()

const toForm = (value: TriggerBinding | null | undefined) => ({
  // 編集を始めた時点の版。その間に他の人が版を作っていたら、サーバーが409で断る
  version: value?.version ?? 0,
  name: value?.name ?? '',
  eventIdPath: value?.eventIdPath ?? '',
  inputs: toTriggerInputRows(value?.inputs),
  maxPayloadKib: value?.maxPayloadBytes ? Math.round(value.maxPayloadBytes / 1024) : DEFAULT_MAX_PAYLOAD_KIB,
  changeReason: '',
})
const form = ref(toForm(modelValue.value))
const reset = () => {
  form.value = toForm(modelValue.value)
}
// 停止・再開・鍵の作り直しで入口が変わっても、未保存の入力は残す（版も編集を始めた時点のまま。その間に別の人が
// 版を作っていれば、保存がサーバーの409で止まる）
watch(modelValue, (value, old) => {
  const dirty = Boolean(old) && JSON.stringify(form.value) !== JSON.stringify(toForm(old))
  if (!(value && old && value.id === old.id && dirty)) form.value = toForm(value)
})

// 取り出せるのはタスクの今の版の入力の項目だけ。型がファイルの項目は、multipartで付けたファイル（$.フィールドの名前）を指す
const taskInputs = computed(() => modelValue.value?.taskInfo.inputs ?? [])
const inputItems = computed(() =>
  taskInputs.value.map((i) => ({
    value: i.name,
    title: i.label ? `${i.label}（${i.name}）` : i.name,
  })),
)
const inputOf = (name: string | null) => taskInputs.value.find((i) => i.name === name)
// タスクから項目を消した後も、保存済みの取り出しは残る（受付時には使わない）
const isMissing = (name: string | null) => Boolean(name) && !inputOf(name)

const addRow = () => form.value.inputs.push({ name: null, path: '' })
const removeRow = (index: number) => form.value.inputs.splice(index, 1)

/**
 * 編集中の受け取り方（保存にも、保存していない設定でサンプルを試すのにも使う）。取り出しと上限は、変えたときだけ
 * 入れる（送らなければサーバーは今の版の値を使う）。サーバーは送られた取り出しをタスクの今の項目で確かめるので、
 * タスクから消した項目の取り出しが残っていても、ほかの設定（名前・上限等）を保存でき、試せるように。上限はKiBで
 * 編集するので、変えていなければ元のバイト数を丸めて送らない
 */
const settings = computed(() => {
  const inputs = fromTriggerInputRows(form.value.inputs)
  const saved = modelValue.value
  const inputsChanged =
    JSON.stringify(inputs) !== JSON.stringify(fromTriggerInputRows(toTriggerInputRows(saved?.inputs)))
  const kib = form.value.maxPayloadKib
  const limitChanged = kib !== null && kib !== toForm(saved).maxPayloadKib
  return {
    eventIdPath: form.value.eventIdPath.trim(),
    inputs: inputsChanged ? inputs : undefined,
    maxPayloadBytes: limitChanged ? kib * 1024 : undefined,
  }
})

const { onSubmit, loading, errors } = createSubmit({
  form,
  fn: (f) => {
    const id = modelValue.value!.id
    // 名前は版の外なので、変えたときだけ送る（古い画面からの保存で、別の人が変えた名前を戻さない）
    const name = f.name.trim() !== modelValue.value?.name ? f.name.trim() : undefined
    return api.manage.triggerBindings(id).patch({
      version: f.version,
      name,
      ...settings.value,
      changeReason: f.changeReason || undefined,
    })
  },
  onSuccess: (result) => {
    emit('updated', result)
    modelValue.value = result
    reset() // 送った変更理由を残さない
    toast.add({ title: t('保存しました'), color: 'success' })
  },
  onError: (err) => {
    emit('failed', err)
    toast.add({ title: t('エラー'), description: makeErrorMsg(err), color: 'error' })
  },
})

const canPost = computed(() => Boolean(form.value.name.trim()))

/** 項目の取り出しの行ごとの誤り（サーバーは行の番号ごとに返す。行に結び付かない誤りは欄の下に出す） */
const rowError = (index: number, field: 'name' | 'path') => rowErrorsOf(errors.value.inputs, index, field)
const inputsErrors = computed(() => {
  const value = errors.value.inputs
  return Array.isArray(value) && value.every((e) => typeof e === 'string') ? (value as string[]) : undefined
})

defineExpose({ onSubmit, loading, canPost, reset, settings })
</script>

<template>
  <DFormStack>
    <DFormAlert :errors="errors" />
    <DTextField v-model="form.name" :label="$t('名前')" required :error-messages="errors.name" />

    <DFormSection
      :title="$t('受け取り方')"
      :description="$t('この欄から下を変えて保存すると、新しい版になり、次に受け付ける要求から使います')"
    >
      <DTextField
        v-model="form.eventIdPath"
        :label="$t('イベントIDの場所')"
        placeholder="$.event.id"
        :hint="
          $t(
            '重複の判定に使う、送り元のイベントIDの場所（JSONのpath）。空なら Idempotency-Key ヘッダーか、推奨の形式の event_id を使います。どれもなければ、送り元が再送すると二重に実行します',
          )
        "
        :error-messages="errors.eventIdPath"
      />

      <div class="grid gap-2">
        <div class="flex items-center gap-2">
          <span class="text-sm font-medium">{{ $t('項目の取り出し') }}</span>
          <div class="flex-1" />
          <DBtn
            size="xs"
            color="neutral"
            variant="outline"
            icon="i-lucide-plus"
            :disabled="!taskInputs.length || form.inputs.length >= MAX_TASK_INPUTS"
            @click="addRow"
          >
            {{ $t('項目を足す') }}
          </DBtn>
        </div>
        <p class="text-xs text-muted">
          {{
            taskInputs.length
              ? $t(
                  'payloadの中の値をタスクの入力の項目にします。空なら、推奨の形式の inputs をそのまま使います。型と必須かはタスクの入力の項目で決まり、合わない要求は400で断ります',
                )
              : $t('タスクに入力の項目がありません。payloadはそのままファイルとしてエージェントに渡します')
          }}
        </p>
        <div
          v-for="(row, index) in form.inputs"
          :key="index"
          class="grid grid-cols-[1fr_1fr_auto] items-start gap-2"
          data-testid="trigger-input-row"
        >
          <DSelect
            v-model="row.name"
            :label="$t('タスクの項目')"
            :show-label="index === 0"
            :items="inputItems"
            :hint="
              isMissing(row.name)
                ? $t('タスクにない項目です。受付時には使いません')
                : inputOf(row.name)?.type === TaskInputType.FILE
                  ? $t('型がファイルの項目は、multipartで付けたファイル（$.フィールドの名前）を指します')
                  : undefined
            "
            :error-messages="rowError(index, 'name')"
          />
          <DTextField
            v-model="row.path"
            :label="$t('payloadの中の場所')"
            :show-label="index === 0"
            placeholder="$.data.vendor_name"
            :error-messages="rowError(index, 'path')"
          />
          <DBtn
            color="neutral"
            variant="ghost"
            icon="i-lucide-x"
            :class="index === 0 ? 'mt-6' : ''"
            :aria-label="$t('この項目を外す')"
            @click="removeRow(index)"
          />
        </div>
        <p v-for="message in inputsErrors" :key="message" class="text-sm text-error">{{ message }}</p>
      </div>

      <DNumberField
        v-model="form.maxPayloadKib"
        :label="$t('payloadの大きさの上限（KiB）')"
        :min="MIN_PAYLOAD_KIB"
        :max="MAX_PAYLOAD_KIB"
        :hint="$t('既定は1024KiB（1MiB）、最大10240KiB（10MiB）。ファイルも含めた本文の大きさです')"
        :error-messages="errors.maxPayloadBytes"
      />
    </DFormSection>

    <DTextarea
      v-model="form.changeReason"
      :label="$t('変更理由')"
      :hint="$t('版の履歴に残ります')"
      :error-messages="errors.changeReason"
    />
  </DFormStack>
</template>
