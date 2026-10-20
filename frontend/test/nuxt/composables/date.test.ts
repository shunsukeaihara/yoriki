import { CalendarDate, Time } from '@internationalized/date'
import { describe, expect, it } from 'vitest'

// vitest.config.ts でタイムゾーンを Asia/Tokyo にしている
describe('日付', () => {
  it("'YYYY-MM-DD' と CalendarDate を相互に変換する", () => {
    expect(isoDateToCalendarDate('2026-10-19')?.toString()).toBe('2026-10-19')
    expect(isoDateToCalendarDate('2026-10-19T09:00:00+09:00')?.toString()).toBe('2026-10-19')
    expect(dateValueToIsoDate(new CalendarDate(2026, 1, 5))).toBe('2026-01-05')
  })

  it('空や不正な値は undefined / null にする', () => {
    expect(isoDateToCalendarDate(null)).toBeUndefined()
    expect(isoDateToCalendarDate('2026-13-45')).toBeUndefined()
    expect(dateValueToIsoDate(null)).toBeNull()
  })
})

describe('日時', () => {
  it('ISO 8601 をローカル時刻にし、送信は UTC の ISO 文字列にする', () => {
    const zoned = isoDateTimeToZoned('2026-10-19T00:30:00Z')
    expect(zoned?.hour).toBe(9)
    expect(zoned?.day).toBe(19)
    expect(dateValueToIsoDateTime(zoned)).toBe('2026-10-19T00:30:00.000Z')
  })

  it('不正な値は undefined にする', () => {
    expect(isoDateTimeToZoned('not a date')).toBeUndefined()
    expect(dateValueToIsoDateTime(undefined)).toBeNull()
  })
})

describe('時刻', () => {
  it("'HH:MM' と Time を相互に変換する", () => {
    expect(isoTimeToTime('09:05')?.toString()).toBe('09:05:00')
    expect(isoTimeToTime('09:05:30')?.minute).toBe(5)
    expect(timeToIsoTime(new Time(18, 30, 15))).toBe('18:30')
    expect(timeToIsoTime(null)).toBeNull()
  })
})

describe('formatDatetime', () => {
  it('ISO 8601 をローカル時刻（Asia/Tokyo）で表示する', () => {
    expect(formatDatetime('2026-10-20T03:04:00Z')).toBe('2026/10/20 12:04')
  })

  it('空や不正な値は空文字にする', () => {
    expect(formatDatetime(null)).toBe('')
    expect(formatDatetime('not-a-date')).toBe('')
  })
})
