<script setup lang="ts">
/** 時刻の入力。値は DRF の TimeField に送れる 'HH:MM' の文字列で受け渡す */
import type { Time } from '@internationalized/date'

const props = withDefaults(
  defineProps<{
    label?: string
    hint?: string
    description?: string
    errorMessages?: string[]
    required?: boolean
    showLabel?: boolean
    class?: ClassProp
  }>(),
  { showLabel: true, required: false },
)
const modelValue = defineModel<string | null>()
defineOptions({ inheritAttrs: false })

const timeValue = computed({
  get: () => isoTimeToTime(modelValue.value),
  set: (value: Time | null | undefined) => {
    modelValue.value = timeToIsoTime(value)
  },
})
</script>

<template>
  <DFormField v-bind="pickFieldProps(props)">
    <UInputTime v-model="timeValue" :hour-cycle="24" class="w-full" v-bind="$attrs" />
  </DFormField>
</template>
