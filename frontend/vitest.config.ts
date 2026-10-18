import { defineVitestConfig } from '@nuxt/test-utils/config'

// 単体テスト（docs/development.md §3.2）。Nuxt の環境（自動 import、Nuxt UI、i18n）で composables と D* ラッパーを動かす
export default defineVitestConfig({
  test: {
    environment: 'nuxt',
    include: ['test/nuxt/**/*.test.ts'],
    // 日時の変換を、実行する環境のタイムゾーンによらず同じ結果にする
    env: { TZ: 'Asia/Tokyo' },
  },
})
