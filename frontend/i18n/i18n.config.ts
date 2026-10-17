// 翻訳キーには日本語の文そのものを使う（例: $t('保存しました')）。
// 日本語の翻訳ファイルにはキーを書かず、キーをそのまま表示する。
// 他の言語を追加するときは、i18n/locales/<言語>.json に「日本語のキー: 訳文」を書く。
export default defineI18nConfig(() => ({
  // 訳がないとき、キーの文を `{count}` などの引数で整形して表示する
  fallbackFormat: true,
  missingWarn: false,
  fallbackWarn: false,
}))
