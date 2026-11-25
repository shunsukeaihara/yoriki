import { createHash } from 'node:crypto'

const INLINE_SCRIPT = /<script\b([^>]*)>([\s\S]*?)<\/script>/gi

/** HTML のインラインのスクリプトのハッシュ（CSP の script-src に書く形）。src のあるものと、実行されない JSON は除く */
export const inlineScriptHashes = (html: string): string[] => {
  const hashes = new Set<string>()
  for (const [, attrs = '', body = ''] of html.matchAll(INLINE_SCRIPT)) {
    if (/\ssrc\s*=/i.test(attrs) || /\stype\s*=\s*["']?application\/json\b/i.test(attrs)) continue
    hashes.add(`'sha256-${createHash('sha256').update(body, 'utf8').digest('base64')}'`)
  }
  return [...hashes]
}

/**
 * 画面の HTML に付ける CSP（docs/deployment.md §4.1）。画面と API は同じオリジンなので、外部への接続は許可しない。
 * インラインのスクリプトは Nuxt が書いたもの（importmap、色モードの初期化、window.__NUXT__）だけを、ハッシュで許可する。
 * スタイルは Nuxt UI と Vue がインラインで入れるので 'unsafe-inline' のまま
 */
export const contentSecurityPolicy = (html: string): string =>
  [
    "default-src 'self'",
    ["script-src 'self'", ...inlineScriptHashes(html)].join(' '),
    "style-src 'self' 'unsafe-inline'",
    "img-src 'self' data: blob:",
    "font-src 'self' data:",
    "connect-src 'self'",
    "frame-ancestors 'none'",
    "base-uri 'self'",
    "object-src 'none'",
    "form-action 'self' https:",
  ].join('; ')
