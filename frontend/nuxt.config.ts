export default defineNuxtConfig({
  compatibilityDate: '2026-10-01',
  ssr: false,
  devtools: { enabled: false },
  modules: ['@nuxt/eslint', '@nuxt/ui', '@nuxtjs/i18n', '@pinia/nuxt', '@vueuse/nuxt'],
  css: ['~/assets/css/main.css'],
  components: {
    dirs: [{ path: '~/components/common', pathPrefix: false }, '~/components'],
  },
  imports: {
    dirs: ['api'],
  },
  typescript: {
    // E2E も型を確かめる（Nuxt の自動 import の対象にはしない）
    nodeTsConfig: { include: ['../e2e/**/*'] },
  },
  i18n: {
    strategy: 'no_prefix',
    defaultLocale: 'ja',
    locales: [{ code: 'ja', language: 'ja-JP', name: '日本語', file: 'ja.json' }],
    detectBrowserLanguage: {
      useCookie: true,
      cookieKey: 'i18n_locale',
      fallbackLocale: 'ja',
    },
  },
  icon: {
    // アイコンはこのサーバーから返し（データは @iconify-json/lucide）、外部（api.iconify.design）へ取りに行かない
    // （ssr: false の既定は外部）。API の既定の /api/_nuxt_icon は、/api/ を Django へ転送するので使えない
    provider: 'server',
    localApiEndpoint: '/_nuxt_icon',
    fallbackToApi: false,
  },
  nitro: {
    // 開発時だけ、/api を Django（uvicorn）へ転送して画面と API を同じオリジンにする。
    // 本番は nginx が同じ役割を持つ（docs/deployment.md §4）
    devProxy: {
      '/api': { target: `${process.env.YORIKI_DEV_API_ORIGIN ?? 'http://localhost:8000'}/api`, xfwd: true },
    },
  },
  runtimeConfig: {
    public: {
      // 画面と同じオリジンの /api/ を使う。別オリジンにはしない（セッション Cookie で認証するため）
      apiEndpoint: '/api/',
    },
  },
})
