<script setup lang="ts">
/** 数値の入力。DRF の IntegerField / FloatField 用で、値は number で受け渡す */
const props = withDefaults(
  defineProps<{
    label?: string
    hint?: string
    description?: string
    errorMessages?: string[]
    required?: boolean
    showLabel?: boolean
    min?: number
    max?: number
    step?: number
    /** Intl.NumberFormat の指定（桁区切り・小数桁など） */
    formatOptions?: Intl.NumberFormatOptions
    class?: ClassProp
  }>(),
  { showLabel: true, required: false },
)
const modelValue = defineModel<number | null>()
// 空にすると UInputNumber は undefined を出す。undefined はJSONで送られないので、null（値なし）にする
const onUpdate = (value: number | null | undefined) => {
  modelValue.value = value ?? null
}
defineOptions({ inheritAttrs: false })
</script>

<template>
  <DFormField v-bind="pickFieldProps(props)">
    <UInputNumber
      :model-value="modelValue"
      :min="min"
      :max="max"
      :step="step"
      :format-options="formatOptions"
      class="w-full"
      v-bind="$attrs"
      @update:model-value="onUpdate"
    />
  </DFormField>
</template>
