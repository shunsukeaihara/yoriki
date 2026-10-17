<script setup lang="ts">
/**
 * 小数の入力。DRF の DecimalField は文字列で受け渡すため、値は string で扱う。
 * 浮動小数の誤差を避けるため、計算はサーバーで行う。
 */
const props = withDefaults(
  defineProps<{
    label?: string
    hint?: string
    description?: string
    errorMessages?: string[]
    required?: boolean
    showLabel?: boolean
    /** 小数点以下の最大桁数（DecimalField の decimal_places） */
    decimalPlaces?: number
    min?: number
    max?: number
    class?: ClassProp
  }>(),
  { decimalPlaces: 3, showLabel: true, required: false },
)
const modelValue = defineModel<string | null>()
defineOptions({ inheritAttrs: false })

const numberValue = computed<number | null>({
  get: () =>
    modelValue.value === null || modelValue.value === undefined || modelValue.value === ''
      ? null
      : Number(modelValue.value),
  set: (value) => {
    modelValue.value =
      value === null || value === undefined || Number.isNaN(value) ? null : value.toFixed(props.decimalPlaces)
  },
})
</script>

<template>
  <DFormField v-bind="pickFieldProps(props)">
    <UInputNumber
      v-model="numberValue"
      :min="min"
      :max="max"
      :step="10 ** -decimalPlaces"
      :format-options="{ minimumFractionDigits: 0, maximumFractionDigits: decimalPlaces }"
      class="w-full"
      v-bind="$attrs"
    />
  </DFormField>
</template>
