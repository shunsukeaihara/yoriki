import { describe, expect, it } from 'vitest'

const t = (key: string) => key
const webhook: SelectableExtension = {
  id: BuiltinExtension.WEBHOOK,
  type: 'notification',
  name: 'Webhook',
  capabilities: ['file_fetch_auth', 'url'],
  configSchema: {},
}
const acme: SelectableExtension = {
  id: 'acme.connector.kintone',
  type: 'connector',
  name: 'kintone',
  capabilities: [],
  configSchema: { properties: { port: { default: 443 } } },
}

describe('plugin', () => {
  it('選択肢は名前で並べ、説明は本体の種別だけに付ける', () => {
    expect(extensionItems([webhook, acme], t)).toEqual([
      { value: BuiltinExtension.WEBHOOK, title: 'Webhook', description: expect.stringContaining('署名') },
      { value: 'acme.connector.kintone', title: 'kintone', description: undefined },
    ])
    expect(extensionItems(null, t)).toEqual([])
  })

  it('対応能力と設定の既定値を読む', () => {
    expect(hasCapability(webhook, Capability.URL)).toBe(true)
    expect(hasCapability(acme, Capability.URL)).toBe(false)
    expect(hasCapability(undefined, Capability.URL)).toBe(false)
    expect(configDefault(acme, 'port')).toBe(443)
    expect(configDefault(webhook, 'port')).toBeUndefined()
  })
})
