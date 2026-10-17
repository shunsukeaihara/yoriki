<script setup lang="ts">
/** 日付の入力。値は DRF の DateField と同じ 'YYYY-MM-DD' の文字列で受け渡す */
import type { DateValue } from '@internationalized/date'

const props = withDefaults(
  defineProps<{
    label?: string
    hint?: string
    description?: string
    errorMessages?: string[]
    required?: boolean
    showLabel?: boolean
    /** 選べる最小の日付（'YYYY-MM-DD'） */
    min?: string
    /** 選べる最大の日付（'YYYY-MM-DD'） */
    max?: string
    class?: ClassProp
  }>(),
  { showLabel: true, required: false },
)
const modelValue = defineModel<string | null>()
defineOptions({ inheritAttrs: false })

const dateValue = computed({
  get: () => isoDateToCalendarDate(modelValue.value),
  set: (value: DateValue | null | undefined) => {
    modelValue.value = dateValueToIsoDate(value)
  },
})
const minValue = computed(() => isoDateToCalendarDate(props.min))
const maxValue = computed(() => isoDateToCalendarDate(props.max))
</script>

<template>
  <DFormField v-bind="pickFieldProps(props)">
    <UInputDate v-model="dateValue" :min-value="minValue" :max-value="maxValue" class="w-full" v-bind="$attrs">
      <template #trailing>
        <UPopover>
          <UButton
            color="neutral"
            variant="link"
            size="sm"
            icon="i-lucide-calendar"
            :aria-label="$t('カレンダーを開く')"
            class="px-0"
          />
          <template #content>
            <UCalendar v-model="dateValue" :min-value="minValue" :max-value="maxValue" class="p-2" />
          </template>
        </UPopover>
      </template>
    </UInputDate>
  </DFormField>
</template>
