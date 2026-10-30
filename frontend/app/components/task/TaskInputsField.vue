<script setup lang="ts">
/**
 * タスクの入力の項目（authorization.md §5.4の入力の制限）。手動実行の画面とWebhookの項目の取り出しは、この定義の
 * 型と必須で値を確かめる。名前はテンプレートの {{ inputs.名前 }} に使う
 */
const props = defineProps<{
  /** 入力の項目の誤り（行ごとの辞書か、項目全体の文の並び） */
  errors?: unknown
}>()
const modelValue = defineModel<TaskInputRow[]>({ required: true })
const { t } = useI18n()

const add = () => modelValue.value.push(emptyInputRow())
const remove = (index: number) => modelValue.value.splice(index, 1)
const move = (index: number, offset: number) => {
  const rows = modelValue.value
  const [row] = rows.splice(index, 1)
  rows.splice(index + offset, 0, row!)
}
// 項目全体の誤り（同じ名前がある、数の上限等）
const fieldErrors = computed(() =>
  Array.isArray(props.errors) ? props.errors.filter((e): e is string => typeof e === 'string') : [],
)
const placeholderExample = (name: string) => `{{ inputs.${name || t('名前')} }}`
</script>

<template>
  <DFormSection
    :title="$t('入力の項目')"
    :description="
      $t('手動実行とWebhookで受け取る値です。依頼文の中に {example} と書くと、値を埋め込みます', {
        example: placeholderExample(''),
      })
    "
  >
    <UAlert v-if="fieldErrors.length" color="error" variant="subtle" :description="fieldErrors.join('、')" />
    <div
      v-for="(row, index) in modelValue"
      :key="index"
      class="grid gap-3 rounded-md border border-default p-3 sm:grid-cols-2"
    >
      <DTextField
        v-model="row.name"
        :label="$t('名前')"
        required
        :hint="placeholderExample(row.name)"
        :error-messages="rowErrorsOf(errors, index, 'name')"
        class="font-mono"
      />
      <DTextField
        v-model="row.label"
        :label="$t('画面に出す名前')"
        required
        :error-messages="rowErrorsOf(errors, index, 'label')"
      />
      <DSelect
        v-model="row.type"
        :label="$t('型')"
        :items="taskInputTypeItems(t)"
        :error-messages="rowErrorsOf(errors, index, 'type')"
      />
      <DCheckbox v-model="row.required" :label="$t('必須')" label-position="top" />
      <DNumberField
        v-if="row.type === TaskInputType.STRING"
        v-model="row.maxLength"
        :label="$t('文字の長さの上限')"
        :min="1"
        :max="10000"
        :error-messages="rowErrorsOf(errors, index, 'maxLength')"
      />
      <DTextarea
        v-if="row.type === TaskInputType.CHOICE"
        v-model="row.choicesText"
        :label="$t('選べる値')"
        :hint="$t('1行に1つ')"
        required
        :rows="3"
        :error-messages="rowErrorsOf(errors, index, 'choices')"
        class="sm:col-span-2"
      />
      <DTextField
        v-model="row.description"
        :label="$t('説明')"
        :error-messages="rowErrorsOf(errors, index, 'description')"
        class="sm:col-span-2"
      />
      <div class="flex justify-end gap-1 sm:col-span-2">
        <DBtn
          color="neutral"
          variant="ghost"
          icon="i-lucide-arrow-up"
          :aria-label="$t('上へ')"
          :disabled="index === 0"
          @click="move(index, -1)"
        />
        <DBtn
          color="neutral"
          variant="ghost"
          icon="i-lucide-arrow-down"
          :aria-label="$t('下へ')"
          :disabled="index === modelValue.length - 1"
          @click="move(index, 1)"
        />
        <DBtn color="error" variant="ghost" icon="i-lucide-trash-2" @click="remove(index)">{{ $t('外す') }}</DBtn>
      </div>
    </div>
    <div>
      <DBtn variant="outline" icon="i-lucide-plus" :disabled="modelValue.length >= MAX_TASK_INPUTS" @click="add">
        {{ $t('項目を足す') }}
      </DBtn>
    </div>
  </DFormSection>
</template>
