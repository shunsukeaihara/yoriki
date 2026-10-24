<script setup lang="ts">
/**
 * SkillのDraft（編集中の中身）の編集。保存では編集を始めた時点の revision を送り、その間に他の人が変えていたら409になる。
 * 使ってよいToolは、公開の権限がある人だけが変えられる（design.md §3.6。サーバーでも確かめる）。
 *
 * 資料の追加・置換でも Draft の revision が増えるので、revision は v-model の Draft に合わせ続け、入力中の文は残す。
 * 中身を Draft から入れ直すのは、別の Draft を開いたときと reset() を呼んだときだけ
 */
const props = defineProps<{ skillId: string; canChangeTools: boolean }>()
const api = useAPI()
const toast = useToast()
const { t } = useI18n()
const modelValue = defineModel<SkillDraft | null>()
const emit = defineEmits<{ updated: [value: SkillDraft]; failed: [error: unknown] }>()

const toForm = (draft: SkillDraft | null | undefined) => ({
  description: draft?.description ?? '',
  instructions: draft?.instructions ?? '',
  tools: [...(draft?.tools ?? [])] as string[],
  requires: [...(draft?.requires ?? [])] as SkillRequirementEnum[],
  inputSchema: schemaToText(draft?.inputSchema),
  outputSchema: schemaToText(draft?.outputSchema),
})
const form = ref(toForm(modelValue.value))
const saved = ref(JSON.stringify(form.value))
const revision = ref(modelValue.value?.revision ?? 0)

// v-model に入れた値は、親が描画し直すまで modelValue に反映されないので、入れ直す Draft を渡せるようにする
const reset = (draft: SkillDraft | null | undefined = modelValue.value) => {
  form.value = toForm(draft)
  saved.value = JSON.stringify(form.value)
  revision.value = draft?.revision ?? 0
  errors.value = {}
}
watch(
  () => modelValue.value?.id,
  () => reset(),
)
watch(
  () => modelValue.value?.revision,
  (value) => {
    if (value) revision.value = value
  },
)

/** 保存していない変更があるか */
const dirty = computed(() => JSON.stringify(form.value) !== saved.value)

// JSONとして読めるかだけを入力中に示す。JSON Schemaとして正しいかはサーバーが確かめる
const inputSchema = computed(() => parseSchemaText(form.value.inputSchema, t))
const outputSchema = computed(() => parseSchemaText(form.value.outputSchema, t))

const { onSubmit, loading, errors } = createSubmit({
  form,
  fn: (data) =>
    api
      .skills(props.skillId)
      .drafts(modelValue.value!.id)
      .patch({
        revision: revision.value,
        description: data.description,
        instructions: data.instructions,
        requires: data.requires,
        inputSchema: inputSchema.value.value,
        outputSchema: outputSchema.value.value,
        // 公開の権限がなければ送らない（同じ値でも、送ると権限を確かめられる）
        ...(props.canChangeTools ? { tools: data.tools as ToolName[] } : {}),
      }),
  onSuccess: (result) => {
    modelValue.value = result
    // サーバーが整えた文（改行等）で入れ直す
    reset(result)
    emit('updated', result)
    toast.add({ title: t('保存しました'), color: 'success' })
  },
  onError: (err) => {
    emit('failed', err)
    toast.add({ title: t('エラー'), description: makeErrorMsg(err), color: 'error' })
  },
})

const canPost = computed(
  () => Boolean(modelValue.value) && dirty.value && !inputSchema.value.error && !outputSchema.value.error,
)

defineExpose({ onSubmit, loading, canPost, dirty, reset })
</script>

<template>
  <DFormStack>
    <DFormAlert :errors="errors" />
    <DTextarea
      v-model="form.description"
      :label="$t('説明')"
      required
      :hint="$t('何をするSkillか、どんな依頼のときに使うか。エージェントと社員がSkillを選ぶのに使います')"
      :error-messages="errors.description"
      class="max-w-none"
    />
    <DTextarea
      v-model="form.instructions"
      :label="$t('手順')"
      required
      :rows="14"
      :hint="$t('エージェントに渡す手順と判断の基準。Markdownで書けます')"
      :error-messages="errors.instructions"
      class="max-w-none font-mono"
    />
    <DCheckboxGroup
      v-model="form.tools"
      :label="$t('使ってよいTool')"
      :hint="
        canChangeTools
          ? $t('何も選ばなければ、コマンドを使わないSkillになります')
          : $t('Toolを変えるには、この範囲の公開の権限が要ります')
      "
      :items="skillToolItems(t)"
      orientation="horizontal"
      :disabled="!canChangeTools"
      :error-messages="errors.tools"
    />
    <DSelect
      v-model="form.requires"
      :label="$t('必要な能力')"
      :hint="$t('この能力を持たないモデルでは、このSkillを使いません')"
      :items="skillRequirementItems(t)"
      multiple
      clearable
      :error-messages="errors.requires"
    />
    <DFormSection :title="$t('入出力のSchema')" :description="$t('JSON Schemaで書きます。空なら決めません')">
      <DTextarea
        v-model="form.inputSchema"
        :label="$t('入力のSchema')"
        :rows="4"
        :error-messages="inputSchema.error ? [inputSchema.error] : errors.inputSchema"
        class="max-w-none font-mono"
      />
      <DTextarea
        v-model="form.outputSchema"
        :label="$t('業務結果のSchema')"
        :rows="4"
        :error-messages="outputSchema.error ? [outputSchema.error] : errors.outputSchema"
        class="max-w-none font-mono"
      />
    </DFormSection>
  </DFormStack>
</template>
