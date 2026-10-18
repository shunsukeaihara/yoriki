<script setup lang="ts">
definePageMeta({ layout: 'anonymous', auth: false })

const { t } = useI18n()
const route = useRoute()
const api = useAPI()
const account = useAccountStore()

// ログイン後に戻る先。外部の URL へは移動させない（バックエンドの allauth でも確認している）
const next = computed(() => {
  const value = route.query.next
  return typeof value === 'string' && value.startsWith('/') && !value.startsWith('//') ? value : '/'
})

// すでにログインしていれば、戻る先へ移動する
if (account.isAuthenticated) await navigateTo(next.value)

const { data: providers, status } = await useAsyncData('auth-providers', () => api.auth.providers.list())

const errorMessage = computed(() => {
  switch (route.query.error) {
    case 'not_allowed':
      return t('このアカウントではログインできません。会社のアカウントでログインしてください。')
    case 'failed':
      return t('ログインに失敗しました。もう一度お試しください。')
    default:
      return null
  }
})

const csrfToken = useCookie('csrftoken')

/** ログインの開始は、CSRF トークンを付けた POST のフォームで送り、画面ごと認証基盤へ移動する */
const login = (provider: AuthProvider) => {
  const form = document.createElement('form')
  form.method = 'POST'
  form.action = provider.loginUrl
  for (const [name, value] of Object.entries({ csrfmiddlewaretoken: csrfToken.value ?? '', next: next.value })) {
    const input = document.createElement('input')
    input.type = 'hidden'
    input.name = name
    input.value = value
    form.appendChild(input)
  }
  document.body.appendChild(form)
  form.submit()
}
</script>

<template>
  <div class="flex min-h-svh items-center justify-center p-4">
    <UCard class="w-full max-w-sm">
      <template #header>
        <h1 class="text-xl font-bold">Yoriki</h1>
        <p class="mt-1 text-sm text-muted">{{ $t('会社のアカウントでログインしてください。') }}</p>
      </template>

      <div class="flex flex-col gap-3">
        <UAlert v-if="errorMessage" color="error" variant="subtle" :title="errorMessage" />
        <USkeleton v-if="status === 'pending'" class="h-10 w-full" />
        <template v-else-if="providers?.length">
          <DBtn v-for="provider in providers" :key="provider.id" block size="lg" @click="login(provider)">
            {{ $t('{name}でログイン', { name: provider.name }) }}
          </DBtn>
        </template>
        <UAlert
          v-else
          color="warning"
          variant="subtle"
          :title="$t('ログインの方法が設定されていません。基盤管理者に連絡してください。')"
        />
      </div>
    </UCard>
  </div>
</template>
