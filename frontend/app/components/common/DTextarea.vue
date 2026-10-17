<script setup lang="ts">
const props = withDefaults(
  defineProps<{
    label?: string
    hint?: string
    description?: string
    errorMessages?: string[]
    required?: boolean
    showLabel?: boolean
    rows?: number
    class?: ClassProp
  }>(),
  { rows: 3, showLabel: true, required: false },
)
const modelValue = defineModel<string | null>()
defineOptions({ inheritAttrs: false })

// Nuxt UI の入力は null を受け付けないため、null は空文字として表示する
const inputValue = computed({
  get: () => modelValue.value ?? '',
  set: (value: string) => {
    modelValue.value = value
  },
})
</script>

<template>
  <DFormField v-bind="pickFieldProps(props)" :class="['max-w-none', props.class]">
    <UTextarea v-model="inputValue" :rows="rows" autoresize class="w-full" v-bind="$attrs" />
  </DFormField>
</template>
