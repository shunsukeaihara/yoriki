<script setup lang="ts">
/**
 * 期間の入力。開始日と終了日を別々の v-model（from / to）で 'YYYY-MM-DD' の文字列として受け渡す。
 * 検索条件の `xxx_from` / `xxx_to` や、モデルの開始日・終了日の組に使う。
 */
import type { DateValue } from '@internationalized/date'

type Range = { start: DateValue | undefined; end: DateValue | undefined }

const props = withDefaults(
  defineProps<{
    label?: string
    hint?: string
    description?: string
    required?: boolean
    showLabel?: boolean
    fromErrorMessages?: string[]
    toErrorMessages?: string[]
    class?: ClassProp
  }>(),
  { showLabel: true, required: false },
)
const from = defineModel<string | null>('from')
const to = defineModel<string | null>('to')
defineOptions({ inheritAttrs: false })

const errorMessages = computed(() => [...(props.fromErrorMessages ?? []), ...(props.toErrorMessages ?? [])])

const range = computed({
  get: (): Range | undefined => {
    const start = isoDateToCalendarDate(from.value)
    const end = isoDateToCalendarDate(to.value)
    return start || end ? { start, end } : undefined
  },
  set: (value: Range | null | undefined) => {
    from.value = dateValueToIsoDate(value?.start)
    to.value = dateValueToIsoDate(value?.end)
  },
})
</script>

<template>
  <DFormField v-bind="{ ...pickFieldProps(props), errorMessages }">
    <UInputDate v-model="range" range class="w-full" v-bind="$attrs">
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
            <UCalendar v-model="range" range :number-of-months="2" class="p-2" />
          </template>
        </UPopover>
      </template>
    </UInputDate>
  </DFormField>
</template>
