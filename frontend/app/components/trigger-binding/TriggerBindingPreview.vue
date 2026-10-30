<script setup lang="ts">
/**
 * サンプルのpayload（JSON）を貼り、受付と同じ処理で、重複のキーと取り出した値・項目ごとの誤りを確かめる（design.md
 * §4.2）。保存していない受け取り方（編集中のイベントIDの場所・項目の取り出し）で試し、何も保存しない。埋め込んだ後の
 * プロンプトは、そのタスクを管理できる人にだけサーバーが返す
 */
const props = defineProps<{ bindingId: string; settings: { eventIdPath: string; inputs: TriggerInput[] } }>()
const api = useAPI()
const toast = useToast()
const { t } = useI18n()

const sample = ref('')
const result = ref<TriggerPreview | null>(null)
// 試した後に受け取り方かサンプルを変えたら、結果が古いことを示す
const tried = ref<string | null>(null)
const current = computed(() => JSON.stringify({ sample: sample.value, settings: props.settings }))
const stale = computed(() => result.value !== null && tried.value !== current.value)

const { onSubmit, loading, errors } = createSubmit({
  form: sample,
  fn: (text) => {
    tried.value = current.value
    return api.manage.triggerBindings(props.bindingId).preview({ sample: text, ...props.settings })
  },
  onSuccess: (value) => (result.value = value),
  onError: (err) => {
    result.value = null
    toast.add({ title: t('エラー'), description: makeErrorMsg(err), color: 'error' })
  },
})

const valueText = (value: unknown) => (typeof value === 'string' ? value : JSON.stringify(value))

defineExpose({ onSubmit })
</script>

<template>
  <div class="grid gap-4">
    <DFormAlert :errors="errors" />
    <DTextarea
      v-model="sample"
      :label="$t('サンプルのpayload（JSON）')"
      :rows="10"
      class="font-mono"
      placeholder='{"event": {"id": "evt-1"}, "data": {"vendor_name": "ACME"}}'
      :hint="
        $t(
          '送り元が送るJSONを貼ります。編集中の受け取り方（保存していなくてもよい）で、受付と同じように取り出します。何も保存せず、タスクも動かしません',
        )
      "
      :error-messages="errors.sample"
    />
    <div class="flex items-center gap-2">
      <DBtn icon="i-lucide-flask-conical" :disabled="!sample.trim()" :loading="loading" @click="onSubmit">
        {{ $t('取り出しを試す') }}
      </DBtn>
      <span v-if="stale" class="text-xs text-warning">
        {{ $t('試した後に受け取り方かサンプルを変えました。もう一度試してください') }}
      </span>
    </div>

    <div v-if="result" class="grid gap-3 text-sm" data-testid="trigger-preview-result">
      <UAlert
        v-if="result.formatError"
        color="error"
        variant="subtle"
        icon="i-lucide-circle-x"
        :title="$t('payloadを読めません（受付では断ります）')"
        :description="result.formatError"
      />
      <template v-else>
        <UAlert
          v-if="result.inputErrors.length"
          color="error"
          variant="subtle"
          icon="i-lucide-circle-x"
          :title="$t('この要求は、受付で400として断ります')"
        >
          <template #description>
            <ul class="list-disc pl-4">
              <li v-for="e in result.inputErrors" :key="e.name">
                <span class="font-mono">{{ e.name }}</span
                >：{{ e.message }}
              </li>
            </ul>
          </template>
        </UAlert>
        <UAlert
          v-else
          color="success"
          variant="subtle"
          icon="i-lucide-circle-check"
          :title="$t('この要求は受け付けられます')"
        />

        <div class="grid gap-1">
          <span class="font-medium">{{ $t('重複の判定のキー') }}</span>
          <span v-if="result.eventIdError" class="text-error">{{ result.eventIdError }}</span>
          <code v-else-if="result.eventId !== null" class="font-mono text-xs">{{ result.eventId }}</code>
          <span v-else class="text-muted">
            {{
              $t(
                'イベントIDがありません。送り元が Idempotency-Key ヘッダーを付けなければ、重複を判定せず、再送すると二重に実行します',
              )
            }}
          </span>
        </div>

        <div class="grid gap-1">
          <span class="font-medium">{{ $t('取り出した値') }}</span>
          <p v-if="!result.values.length" class="text-muted">{{ $t('ありません') }}</p>
          <dl v-else class="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1">
            <template v-for="v in result.values" :key="v.name">
              <dt class="font-mono text-muted">{{ v.name }}</dt>
              <dd class="break-all whitespace-pre-wrap">{{ valueText(v.value) }}</dd>
            </template>
          </dl>
          <p v-if="result.fileInputs.length" class="text-xs text-muted">
            {{
              $t('型がファイルの項目（{names}）は、multipartで付けたファイルを指すので、ここでは試せません', {
                names: result.fileInputs.join(', '),
              })
            }}
          </p>
        </div>

        <div class="grid gap-1">
          <span class="font-medium">{{ $t('埋め込んだ後のプロンプト') }}</span>
          <!-- 断られる要求では、Runを作らないのでプロンプトもない（欠けた値で埋めたものを見せない） -->
          <p v-if="result.inputErrors.length" class="text-muted">
            {{ $t('受付で断るので、プロンプトは作りません') }}
          </p>
          <pre
            v-else-if="result.prompt !== null"
            class="max-h-80 overflow-auto rounded-md bg-elevated p-3 text-xs whitespace-pre-wrap"
            >{{ result.prompt }}</pre>
          <p v-else class="text-muted">
            {{ $t('このタスクを管理する権限がないので、タスクの依頼文は表示しません') }}
          </p>
        </div>
      </template>
    </div>
  </div>
</template>
