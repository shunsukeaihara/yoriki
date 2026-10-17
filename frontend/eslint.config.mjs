// ESLint は不具合につながる書き方の検出だけを担当し、見た目の整形は Prettier に任せる
import prettier from 'eslint-config-prettier'
import withNuxt from './.nuxt/eslint.config.mjs'

export default withNuxt(
  {
    rules: {
      // TypeScript の省略可能な props は undefined が既定値なので、default の記述を求めない
      'vue/require-default-prop': 'off',
    },
  },
  prettier,
)
