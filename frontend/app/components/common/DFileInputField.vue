<script setup lang="ts">
/** ファイルの選択。ドラッグ＆ドロップにも対応する。送信方法（multipart 等）は API 側で決める */
const props = withDefaults(
  defineProps<{
    label?: string
    hint?: string
    description?: string
    errorMessages?: string[]
    required?: boolean
    showLabel?: boolean
    accept?: string
    multiple?: boolean
    /** 画像のプレビューを出す */
    preview?: boolean
    class?: ClassProp
  }>(),
  { accept: '*', multiple: false, preview: true, showLabel: true, required: false },
)
const modelValue = defineModel<File | File[] | null>()
defineOptions({ inheritAttrs: false })
</script>

<template>
  <DFormField v-bind="pickFieldProps(props)">
    <UFileUpload
      :model-value="(modelValue ?? undefined) as any"
      :accept="accept"
      :multiple="multiple"
      :preview="preview"
      :label="$t('ファイルをドロップ')"
      :description="$t('またはクリックして選択')"
      class="w-full min-h-32"
      v-bind="$attrs"
      @update:model-value="(v: unknown) => (modelValue = (v ?? null) as File | File[] | null)"
    />
  </DFormField>
</template>
