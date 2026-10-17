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
