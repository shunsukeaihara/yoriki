<script setup lang="ts">
/** 少数の選択肢をボタンの並びで選ぶ */
type Value = string | number | boolean

const props = withDefaults(
  defineProps<{
    label?: string
    hint?: string
    description?: string
    errorMessages?: string[]
    required?: boolean
    showLabel?: boolean
    items: unknown[]
    itemValue?: ItemAccessor
    itemTitle?: ItemAccessor
    class?: ClassProp
  }>(),
  { itemValue: 'value', itemTitle: 'title', showLabel: true, required: false },
)
const modelValue = defineModel<Value | null>()
defineOptions({ inheritAttrs: false })

const options = computed(() =>
  props.items.map((item) => ({
    value: getProp(item, props.itemValue) as Value,
    label: String(getProp(item, props.itemTitle) ?? ''),
  })),
)
</script>

<template>
  <DFormField v-bind="pickFieldProps(props)">
    <UFieldGroup v-bind="$attrs">
      <UButton
        v-for="o in options"
        :key="String(o.value)"
        :label="o.label"
        color="neutral"
        :variant="modelValue === o.value ? 'solid' : 'outline'"
        :aria-pressed="modelValue === o.value"
        @click="modelValue = o.value"
      />
    </UFieldGroup>
  </DFormField>
</template>
