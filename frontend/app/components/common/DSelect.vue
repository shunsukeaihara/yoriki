<script setup lang="ts">
/**
 * 固定の選択肢から選ぶ。choices のように件数が少なく、API で検索しないもの。
 * items は任意のオブジェクトで、itemValue / itemTitle で値と表示名を取り出す（既定は { value, title }）。
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
    multiple?: boolean
    /** 選択を外すボタンを出す */
    clearable?: boolean
    /** 選択肢を絞り込む入力欄を出す */
    searchable?: boolean
    placeholder?: string
    class?: ClassProp
  }>(),
  {
    itemValue: 'value',
    itemTitle: 'title',
    multiple: false,
    clearable: false,
    searchable: false,
    showLabel: true,
    required: false,
  },
)
const modelValue = defineModel<Value | Value[] | null>()
const emit = defineEmits<{ selected: [item: unknown] }>()
defineOptions({ inheritAttrs: false })

const options = computed(() =>
  props.items.map((item) => ({
    value: getProp(item, props.itemValue) as Value,
    label: String(getProp(item, props.itemTitle) ?? ''),
    description: props.itemDescription ? String(getProp(item, props.itemDescription) ?? '') : undefined,
    raw: item,
  })),
)

const onUpdate = (value: unknown) => {
  modelValue.value = (value ?? null) as Value | Value[] | null
  if (!props.multiple) emit('selected', options.value.find((o) => o.value === value)?.raw ?? null)
}
</script>

<template>
  <DFormField v-bind="pickFieldProps(props)">
    <USelectMenu
      :model-value="(modelValue ?? undefined) as any"
      :items="options"
      value-key="value"
      :multiple="multiple"
      :clear="clearable"
      :search-input="searchable ? { placeholder: $t('検索') } : false"
      :placeholder="placeholder ?? $t('選択してください')"
      class="w-full"
      v-bind="$attrs"
      @update:model-value="onUpdate"
    >
      <template v-if="$slots.option" #item="{ item }">
        <slot name="option" :item="(item as (typeof options)[number]).raw" />
      </template>
    </USelectMenu>
  </DFormField>
</template>
