<script setup lang="ts">
/**
 * 選択肢を並べて複数選ぶ。variant="card" で説明付きのカードになる。値は文字列（Nuxt UI の UCheckboxGroup の制約）。
 */
type Value = string

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
    itemDescription?: ItemAccessor
    variant?: 'list' | 'card' | 'table'
    orientation?: 'horizontal' | 'vertical'
    class?: ClassProp
  }>(),
  {
    itemValue: 'value',
    itemTitle: 'title',
    itemDescription: 'description',
    variant: 'list',
    orientation: 'vertical',
    showLabel: true,
    required: false,
  },
)
const modelValue = defineModel<Value[]>({ default: () => [] })
defineOptions({ inheritAttrs: false })

const options = computed(() =>
  props.items.map((item) => ({
    value: String(getProp(item, props.itemValue)),
    label: String(getProp(item, props.itemTitle) ?? ''),
    description: (getProp(item, props.itemDescription) as string | undefined) || undefined,
    disabled: Boolean(getProp(item, 'disabled')),
  })),
)
</script>

<template>
  <DFormField v-bind="pickFieldProps(props)" :class="['max-w-none', props.class]">
    <UCheckboxGroup
      :model-value="modelValue"
      :items="options"
      value-key="value"
      :variant="variant"
      :orientation="orientation"
      v-bind="$attrs"
      @update:model-value="(v: unknown) => (modelValue = v as Value[])"
    />
  </DFormField>
</template>
