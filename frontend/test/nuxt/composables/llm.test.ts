import { describe, expect, it } from 'vitest'

describe('ProviderKind', () => {
  it('base URL を指定するのも、API key を省けるのもローカルLLMだけ', () => {
    expect(usesBaseUrl(ProviderKind.LOCAL)).toBe(true)
    expect(requiresApiKey(ProviderKind.LOCAL)).toBe(false)
    for (const kind of [ProviderKind.OPENAI, ProviderKind.ANTHROPIC, ProviderKind.GEMINI]) {
      expect(usesBaseUrl(kind)).toBe(false)
      expect(requiresApiKey(kind)).toBe(true)
    }
  })
})

describe('formatPrice', () => {
  it('DecimalField の文字列を米ドルで表示し、小数点以下6桁まで残す', () => {
    expect(formatPrice('2.500000')).toBe('$2.50')
    expect(formatPrice('0.000125')).toBe('$0.000125')
    expect(formatPrice('')).toBe('')
    expect(formatPrice(null)).toBe('')
  })
})
