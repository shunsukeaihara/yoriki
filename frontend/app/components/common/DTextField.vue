<script setup lang="ts">
const props = withDefaults(
  defineProps<{
    label?: string
    hint?: string
    description?: string
    errorMessages?: string[]
    required?: boolean
    showLabel?: boolean
    type?: string
    class?: ClassProp
  }>(),
  { type: 'text', showLabel: true, required: false },
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
  <DFormField v-bind="pickFieldProps(props)">
    <UInput v-model="inputValue" :type="type" class="w-full" v-bind="$attrs">
      <template v-if="$slots.prepend" #leading><slot name="prepend" /></template>
      <template v-if="$slots.append" #trailing><slot name="append" /></template>
    </UInput>
  </DFormField>
</template>
