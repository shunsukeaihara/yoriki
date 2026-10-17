<script setup lang="ts">
/**
 * 日時の入力。値は DRF の DateTimeField と同じ ISO 8601 の文字列で受け渡す。
 * 表示と入力は利用者のローカルタイムゾーンで行い、送信時は UTC の ISO 文字列にする。
 */
import { CalendarDateTime, getLocalTimeZone, toCalendarDate, toZoned, type DateValue } from '@internationalized/date'

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

const zonedValue = computed({
  get: () => isoDateTimeToZoned(modelValue.value),
  set: (value: DateValue | null | undefined) => {
    modelValue.value = dateValueToIsoDateTime(value)
  },
})

// カレンダーで日付を選んだときは、入力済みの時刻を保ったまま日付だけを変える
const calendarValue = computed(() => (zonedValue.value ? toCalendarDate(zonedValue.value) : undefined))
const onCalendarSelect = (selected: unknown) => {
  const value = selected as DateValue | null | undefined
  if (!value) return
  const base = zonedValue.value
  zonedValue.value = base
    ? base.set({ year: value.year, month: value.month, day: value.day })
    : toZoned(new CalendarDateTime(value.year, value.month, value.day, 0, 0), getLocalTimeZone())
}
</script>

<template>
  <DFormField v-bind="pickFieldProps(props)">
    <UInputDate
      v-model="zonedValue"
      granularity="minute"
      :hour-cycle="24"
      hide-time-zone
      class="w-full"
      v-bind="$attrs"
    >
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
            <UCalendar :model-value="calendarValue" class="p-2" @update:model-value="onCalendarSelect" />
          </template>
        </UPopover>
      </template>
    </UInputDate>
  </DFormField>
</template>
