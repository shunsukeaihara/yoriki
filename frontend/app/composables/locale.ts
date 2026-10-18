import * as uiLocales from '@nuxt/ui/locale'

/** Nuxt UI のコンポーネント内の文言・日付書式を、アプリの言語に合わせる（UApp の locale に渡す） */
export const useUiLocale = () => {
  const { locale } = useI18n()
  const uiLocale = computed(() => uiLocales[locale.value as keyof typeof uiLocales] ?? uiLocales.ja)

  useHead({
    htmlAttrs: {
      lang: () => uiLocale.value.code,
      dir: () => uiLocale.value.dir,
    },
  })
  return uiLocale
}
