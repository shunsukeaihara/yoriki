<script setup lang="ts">
// D*ラッパーの見た目とエラー表示を確認するための開発用ページ
definePageMeta({
  // ログインせずに開くので、会話の一覧（API を呼ぶ）を出す社員の layout は使わない
  layout: 'anonymous',
  auth: false,
  middleware: () => {
    if (!import.meta.dev) return abortNavigation({ statusCode: 404 })
  },
})

const { t } = useI18n()
const confirm = useConfirmDialog()

type Sample = { id: string; name: string; email: string }
const samples: Sample[] = Array.from({ length: 45 }, (_, i) => ({
  id: `u${i + 1}`,
  name: `ユーザー${i + 1}`,
  email: `user${i + 1}@example.com`,
}))
// API の代わりに、検索語で絞り込んだ結果を1ページで返す
const searchSamples = async (params: Record<string, unknown>): Promise<Paginated<Sample>> => {
  const q = String(params.search ?? '')
  return { results: samples.filter((s) => s.name.includes(q)), next: null, previous: null }
}

const choices = [
  { value: 1, title: 'Webhook', description: 'HTTPSのURLへ送信する' },
  { value: 2, title: 'Slack', description: 'チャネルへ投稿する' },
  { value: 3, title: 'Teams', description: 'チャネルへ投稿する' },
]

const form = ref({
  text: '',
  textarea: '',
  number: null as number | null,
  decimal: '12.500' as string | null,
  select: null as number | null,
  autocomplete: null as string | number | null,
  autocompleteMultiple: [] as (string | number)[],
  checkbox: false,
  switch: true,
  radio: 1 as number | null,
  radioCard: 2 as number | null,
  checkboxGroup: ['1'] as string[],
  checkboxGroupCard: [] as string[],
  selectButton: 1 as number | null,
  date: '2026-10-16' as string | null,
  datetime: '2026-10-16T09:30:00+09:00' as string | null,
  dateFrom: null as string | null,
  dateTo: null as string | null,
  time: '09:00' as string | null,
  file: null as File | File[] | null,
})

const showErrors = ref(false)
const errors = computed<ValidationErrors>(() => {
  if (!showErrors.value) return {}
  const e = [t('この項目は必須です。')]
  return {
    nonFieldErrors: [t('他の利用者が先に更新しました。')],
    ...Object.fromEntries(Object.keys(form.value).map((k) => [k, e])),
  }
})

const confirmResult = ref<boolean | null>(null)
const openConfirm = async () => {
  confirmResult.value = await confirm({ title: t('削除しますか？'), color: 'error' })
}
</script>

<template>
  <UContainer class="grid gap-6 py-8">
    <div>
      <h1 class="text-xl font-bold">{{ $t('入力コンポーネント一覧') }}</h1>
      <p class="text-sm text-muted">{{ $t('D*ラッパーの表示確認用ページ（開発時のみ）') }}</p>
    </div>
    <DSwitch v-model="showErrors" :label="$t('エラー表示を確認する')" />

    <div class="grid gap-8 lg:grid-cols-[1fr_20rem]">
      <DFormStack>
        <DFormAlert :errors="errors" />
        <DFormSection title="Text">
          <DTextField
            v-model="form.text"
            :label="$t('テキスト')"
            :hint="$t('入力欄の下に出る補足')"
            required
            :error-messages="errors.text"
          />
          <DTextarea v-model="form.textarea" :label="$t('複数行テキスト')" :error-messages="errors.textarea" />
          <DNumberField v-model="form.number" :label="$t('数値')" :error-messages="errors.number" />
          <DDecimalField
            v-model="form.decimal"
            :label="$t('小数（DecimalField）')"
            :decimal-places="3"
            :error-messages="errors.decimal"
          />
        </DFormSection>

        <DFormSection title="Select">
          <DSelect
            v-model="form.select"
            :label="$t('選択')"
            :items="choices"
            clearable
            :error-messages="errors.select"
          />
          <DAutocomplete
            v-model="form.autocomplete"
            :label="$t('検索して選択')"
            :search-fn="searchSamples"
            item-description="email"
            :initial-selected="samples[2]"
            :error-messages="errors.autocomplete"
          />
          <DAutocomplete
            v-model="form.autocompleteMultiple"
            :label="$t('検索して複数選択')"
            :search-fn="searchSamples"
            multiple
            :error-messages="errors.autocompleteMultiple"
          />
          <DCheckbox v-model="form.checkbox" :label="$t('チェックボックス')" :error-messages="errors.checkbox" />
          <DRadioGroup v-model="form.radio" :label="$t('ラジオ')" :items="choices" :error-messages="errors.radio" />
          <DRadioGroup
            v-model="form.radioCard"
            :label="$t('ラジオ（カード）')"
            :items="choices"
            variant="card"
            orientation="horizontal"
            :error-messages="errors.radioCard"
          />
          <DCheckboxGroup
            v-model="form.checkboxGroup"
            :label="$t('複数選択')"
            :items="choices"
            :error-messages="errors.checkboxGroup"
          />
          <DCheckboxGroup
            v-model="form.checkboxGroupCard"
            :label="$t('複数選択（カード）')"
            :items="choices"
            variant="card"
            :error-messages="errors.checkboxGroupCard"
          />
          <DSelectButton
            v-model="form.selectButton"
            :label="$t('ボタンで選択')"
            :items="choices"
            :error-messages="errors.selectButton"
          />
        </DFormSection>

        <DFormSection title="Date / Time">
          <DDatePicker v-model="form.date" :label="$t('日付')" :error-messages="errors.date" />
          <DDateTimePicker v-model="form.datetime" :label="$t('日時')" :error-messages="errors.datetime" />
          <DDateRange
            v-model:from="form.dateFrom"
            v-model:to="form.dateTo"
            :label="$t('期間')"
            :from-error-messages="errors.dateFrom"
          />
          <DTimeField v-model="form.time" :label="$t('時刻')" :error-messages="errors.time" />
        </DFormSection>

        <DFormSection title="File">
          <DFileInputField v-model="form.file" :label="$t('ファイル')" :error-messages="errors.file" />
        </DFormSection>

        <DFormSection title="Dialog">
          <div class="flex gap-2">
            <DDialog>
              <template #trigger="{ on }">
                <DBtn v-on="on">{{ $t('ダイアログを開く') }}</DBtn>
              </template>
              <template #header>{{ $t('ダイアログを開く') }}</template>
              <template #content>
                <DTextField v-model="form.text" :label="$t('テキスト')" />
              </template>
              <template #footer="{ closeDialog }">
                <DBtn color="neutral" variant="ghost" @click="closeDialog()">{{ $t('キャンセル') }}</DBtn>
                <DBtn @click="closeDialog()">{{ $t('保存') }}</DBtn>
              </template>
            </DDialog>
            <DBtn color="error" variant="outline" @click="openConfirm">{{ $t('確認ダイアログ') }}</DBtn>
          </div>
          <p v-if="confirmResult !== null" class="text-sm">
            {{ $t('確認の結果: {result}', { result: String(confirmResult) }) }}
          </p>
        </DFormSection>
      </DFormStack>

      <UCard class="self-start lg:sticky lg:top-4">
        <template #header>{{ $t('現在の値') }}</template>
        <pre class="text-xs whitespace-pre-wrap break-all">{{ JSON.stringify(form, null, 2) }}</pre>
      </UCard>
    </div>
  </UContainer>
</template>
