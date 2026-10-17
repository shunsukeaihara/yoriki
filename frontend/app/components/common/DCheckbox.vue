<script setup lang="ts">
const props = withDefaults(
  defineProps<{
    label?: string
    hint?: string
    description?: string
    errorMessages?: string[]
    required?: boolean
    showLabel?: boolean
    /** inline: チェックボックスの右にラベル（既定） / top: 他の入力と同じくラベルを上に置く */
    labelPosition?: 'inline' | 'top'
    class?: ClassProp
  }>(),
  { labelPosition: 'inline', showLabel: true, required: false },
)
const modelValue = defineModel<boolean>({ default: false })
defineOptions({ inheritAttrs: false })
const fieldProps = computed(() => ({
  ...pickFieldProps(props),
  label: props.labelPosition === 'top' ? props.label : undefined,
}))
</script>

<template>
  <DFormField v-bind="fieldProps">
    <UCheckbox
      v-model="modelValue"
      :label="labelPosition === 'inline' && showLabel ? label : undefined"
      v-bind="$attrs"
    />
  </DFormField>
</template>
