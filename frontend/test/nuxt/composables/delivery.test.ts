import { describe, expect, it } from 'vitest'

describe('delivery', () => {
  it('送り先のURLを持つのはWebhookとZapierだけ', () => {
    expect(isUrlDestinationKind(DestinationKind.WEBHOOK)).toBe(true)
    expect(isUrlDestinationKind(DestinationKind.ZAPIER)).toBe(true)
    expect(isUrlDestinationKind(DestinationKind.IN_APP)).toBe(false)
    expect(isUrlDestinationKind(null)).toBe(false)
  })

  it('人が送り直せるのは、失敗・確認待ち・取消の配信だけ', () => {
    const retryable = Object.values(DeliveryStatus).filter((status) => isRetryableDelivery({ status }))
    expect(retryable).toEqual([DeliveryStatus.FAILED, DeliveryStatus.UNKNOWN, DeliveryStatus.CANCELLED])
  })

  it('項目の取り出しは、名前と場所の両方がある行だけを送り、場所の前後の空白を除く', () => {
    const rows = toTriggerInputRows([{ name: 'vendor', path: '$.vendor' }])
    rows.push({ name: null, path: '$.amount' }, { name: 'amount', path: '  ' }, { name: 'memo', path: ' $.memo ' })
    expect(fromTriggerInputRows(rows)).toEqual([
      { name: 'vendor', path: '$.vendor' },
      { name: 'memo', path: '$.memo' },
    ])
  })
})
