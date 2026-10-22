import MarkdownIt from 'markdown-it'

/**
 * エージェントの応答（Markdown）を HTML にする。応答には Tool の結果を通じて外から来た文が混ざりうるので、
 * 信頼できない入力として扱う：生の HTML は書いたまま表示し（html: false）、画像は読み込まない（URL に情報を載せて
 * 外へ送られるのを防ぐ）。リンクは markdown-it が javascript: 等を除き、新しいタブで開く。
 */
const md = new MarkdownIt({ html: false, linkify: true, breaks: true })
md.disable('image')

const renderToken: NonNullable<typeof md.renderer.rules.link_open> = (tokens, idx, options, _env, self) =>
  self.renderToken(tokens, idx, options)
const renderLinkOpen = md.renderer.rules.link_open ?? renderToken
md.renderer.rules.link_open = (tokens, idx, options, env, self) => {
  tokens[idx]!.attrSet('target', '_blank')
  tokens[idx]!.attrSet('rel', 'noopener noreferrer')
  return renderLinkOpen(tokens, idx, options, env, self)
}

export const renderMarkdown = (text: string): string => md.render(text)
