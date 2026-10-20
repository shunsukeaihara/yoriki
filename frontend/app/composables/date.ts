import {
  CalendarDate,
  type Time,
  getLocalTimeZone,
  parseAbsoluteToLocal,
  parseDate,
  parseTime,
  type DateValue,
  type ZonedDateTime,
} from '@internationalized/date'

// DRF とやり取りする日付・時刻の文字列と、Nuxt UI の入力コンポーネントが扱う値を相互に変換する。
// - DateField / date: 'YYYY-MM-DD'
// - DateTimeField: ISO 8601（例: '2026-10-16T09:00:00+09:00'）
// - TimeField: 'HH:MM' または 'HH:MM:SS'

export const isoDateToCalendarDate = (value: string | null | undefined): CalendarDate | undefined => {
  if (!value) return undefined
  try {
    return parseDate(value.slice(0, 10))
  } catch {
    return undefined
  }
}

export const dateValueToIsoDate = (value: DateValue | null | undefined): string | null =>
  value ? new CalendarDate(value.year, value.month, value.day).toString() : null

export const isoDateTimeToZoned = (value: string | null | undefined): ZonedDateTime | undefined => {
  if (!value) return undefined
  try {
    return parseAbsoluteToLocal(value)
  } catch {
    return undefined
  }
}

export const dateValueToIsoDateTime = (value: DateValue | null | undefined): string | null =>
  value ? value.toDate(getLocalTimeZone()).toISOString() : null

export const isoTimeToTime = (value: string | null | undefined): Time | undefined => {
  if (!value) return undefined
  try {
    return parseTime(value)
  } catch {
    return undefined
  }
}

export const timeToIsoTime = (value: Time | null | undefined): string | null =>
  value ? value.toString().slice(0, 5) : null

/** DateTimeField（ISO 8601）を、現在の言語の書式でローカル時刻として表示する */
export const formatDatetime = (value: string | null | undefined): string => {
  if (!value) return ''
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return ''
  const { $i18n } = useNuxtApp()
  return new Intl.DateTimeFormat($i18n.locale.value, { dateStyle: 'medium', timeStyle: 'short' }).format(date)
}
