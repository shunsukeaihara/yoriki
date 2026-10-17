<script setup lang="ts">
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
const modelValue = defineModel<boolean>({ default: false })
defineOptions({ inheritAttrs: false })
</script>

<template>
  <DFormField v-bind="{ ...pickFieldProps(props), label: undefined }">
    <USwitch v-model="modelValue" :label="showLabel ? label : undefined" v-bind="$attrs" />
  </DFormField>
</template>
