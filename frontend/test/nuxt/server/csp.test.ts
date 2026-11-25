import { createHash } from 'node:crypto'
import { describe, expect, it } from 'vitest'
import { contentSecurityPolicy, inlineScriptHashes } from '../../../server/utils/csp'

const hash = (text: string) => `'sha256-${createHash('sha256').update(text).digest('base64')}'`

describe('inlineScriptHashes', () => {
  it('インラインのスクリプトと importmap を、書かれたままの中身でハッシュにする', () => {
    const html = '<script type="importmap">{"imports":{}}</script><body><script>\nconst a = "日本語"\n</script></body>'
    expect(inlineScriptHashes(html)).toEqual([hash('{"imports":{}}'), hash('\nconst a = "日本語"\n')])
  })

  it('src のあるものと JSON は除き、同じ中身は1つにする', () => {
    const html =
      '<script type="module" src="/_nuxt/a.js" crossorigin></script>' +
      '<script type="application/json" data-src="x">[1]</script><script>a()</script><script>a()</script>'
    expect(inlineScriptHashes(html)).toEqual([hash('a()')])
  })
})

describe('contentSecurityPolicy', () => {
  it("script-src に 'unsafe-inline' を含めない", () => {
    const scriptSrc = contentSecurityPolicy('<script>a()</script>')
      .split('; ')
      .find((directive) => directive.startsWith('script-src'))
    expect(scriptSrc).toBe(`script-src 'self' ${hash('a()')}`)
  })
})
