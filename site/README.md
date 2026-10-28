# site/ — ランディングページ

Yorikiを紹介する静的なランディングページ。英語を基本とし、日本語に切り替えられる。

- ビルドは不要。`index.html`、`assets/style.css`、`assets/app.js`（テーマの切替・スクロール時の表示）、`assets/i18n.js`（日本語の辞書と言語の切替）、`assets/favicon.svg` だけで動く。
- 言語は、`?lang=ja` / `?lang=en` → `localStorage` → ブラウザの言語の順で決める。画面の文言は英語を正本としてHTMLに書き、`data-i18n` を付けた要素を `assets/i18n.js` の辞書（英語の文がキー）で日本語に置き換える。文言を足したら辞書にも足す。
- 配色のトークンは `style.css` の `:root` にあり、ダークモードは `prefers-color-scheme` とヘッダーのボタン（`data-theme`）の両方に対応する。
- 手元で見るには、`site/` で `python3 -m http.server 8765` を動かして http://localhost:8765/ を開く。
- 整形は `pnpm exec prettier --print-width 120 --no-semi --single-quote --write "site/**/*.{html,css,js}"`（`frontend/` のPrettierを使う）。
- 配備は `.github/workflows/site.yml`。`main` への `site/` の push でGitHub Pagesへ配備する（手動実行もできる）。自動の配備を止めたいときは、Actionsの画面でこのworkflowをDisableにする。Pagesの Source は「GitHub Actions」にしておく。この `README.md` は配備に含めない。
- `site/` だけの変更のコミットに `[skip ci]` は付けない。他のworkflowは `paths` で動かず、付けると `site` も動かなくなる。
