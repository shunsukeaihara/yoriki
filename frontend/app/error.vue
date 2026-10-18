<script setup lang="ts">
import type { NuxtError } from '#app'

const props = defineProps<{ error: NuxtError }>()
const { t } = useI18n()
// error.vue は app.vue の代わりに描画されるので、UApp もここで置く
const uiLocale = useUiLocale()

const title = computed(() => {
  switch (props.error.statusCode) {
    case 403:
      return t('このページを表示する権限がありません。')
    case 404:
      return t('ページが見つかりません。')
    default:
      return t('エラーが発生しました。')
  }
})

// API のエラー（plugins/error-handler.ts）はサーバーの detail を表示する。
// それ以外の message は利用者向けの文ではない（英語の内部メッセージ等）ので表示しない
const detail = computed(() => {
  const data = props.error.data as { detail?: unknown } | undefined
  return typeof data?.detail === 'string' ? data.detail : null
})

const guide = computed(() => {
  switch (props.error.statusCode) {
    case 403:
      return t('必要な場合は、管理者に権限の付与を依頼してください。')
    case 404:
      return t('URLが正しいか確認してください。')
    default:
      return t('時間をおいて再度お試しください。解決しない場合は管理者に問い合わせてください。')
  }
})

const goHome = () => clearError({ redirect: '/' })
const reload = () => window.location.reload()
</script>

<template>
  <UApp :locale="uiLocale">
    <div class="flex min-h-svh items-center justify-center bg-default p-4">
      <UCard class="w-full max-w-md">
        <template #header>
          <p class="text-sm text-muted">{{ error.statusCode }}</p>
          <h1 class="mt-1 text-xl font-bold">{{ title }}</h1>
        </template>
        <p v-if="detail" class="mb-2 text-sm">{{ detail }}</p>
        <p class="text-sm text-muted">{{ guide }}</p>
        <template #footer>
          <div class="flex justify-end gap-2">
            <DBtn variant="ghost" color="neutral" icon="i-lucide-refresh-cw" @click="reload">
              {{ $t('再読み込み') }}
            </DBtn>
            <DBtn icon="i-lucide-house" @click="goHome">{{ $t('トップへ戻る') }}</DBtn>
          </div>
        </template>
      </UCard>
    </div>
  </UApp>
</template>
