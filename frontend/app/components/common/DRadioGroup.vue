<script setup lang="ts">
/**
 * 選択肢を並べて1つ選ぶ。variant="card" で説明付きのカードになる。
 */
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
const modelValue = defineModel<Value | null>()
defineOptions({ inheritAttrs: false })

const options = computed(() =>
  props.items.map((item) => ({
    value: getProp(item, props.itemValue) as Value,
    label: String(getProp(item, props.itemTitle) ?? ''),
    description: (getProp(item, props.itemDescription) as string | undefined) || undefined,
    disabled: Boolean(getProp(item, 'disabled')),
  })),
)
</script>

<template>
  <DFormField v-bind="pickFieldProps(props)" :class="['max-w-none', props.class]">
    <URadioGroup
      :model-value="(modelValue ?? undefined) as any"
      :items="options"
      value-key="value"
      :variant="variant"
      :orientation="orientation"
      v-bind="$attrs"
      @update:model-value="(v: unknown) => (modelValue = v as Value)"
    />
  </DFormField>
</template>
