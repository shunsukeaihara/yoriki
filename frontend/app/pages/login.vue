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

const { data: methods, status } = await useAsyncData('auth-methods', () => api.auth.methods.get())
const providers = computed(() => methods.value?.providers ?? [])
// SSO が有効な間は、パスワードの欄を出さない。基盤管理者は /login?method=password で使う（設定の誤りや認証基盤の停止に備える）
const showPassword = computed(() => Boolean(methods.value?.passwordLogin) || route.query.method === 'password')

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

/** SSO のログインの開始は、CSRF トークンを付けた POST のフォームで送り、画面ごと認証基盤へ移動する */
const loginWithSSO = (provider: AuthProvider) => {
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

const form = ref<Login>({ email: '', password: '' })
const { onSubmit, loading, errors } = createSubmit({
  form,
  fn: (data) => account.login(data),
  onSuccess: () => navigateTo(next.value),
})
</script>

<template>
  <div class="flex min-h-svh items-center justify-center p-4">
    <UCard class="w-full max-w-sm">
      <template #header>
        <h1 class="text-xl font-bold">Yoriki</h1>
        <p class="mt-1 text-sm text-muted">
          {{ providers.length ? $t('会社のアカウントでログインしてください。') : $t('ログインしてください。') }}
        </p>
      </template>

      <div class="flex flex-col gap-3">
        <UAlert v-if="errorMessage" color="error" variant="subtle" :title="errorMessage" />
        <USkeleton v-if="status === 'pending'" class="h-10 w-full" />
        <template v-else>
          <DBtn v-for="provider in providers" :key="provider.id" block size="lg" @click="loginWithSSO(provider)">
            {{ $t('{name}でログイン', { name: provider.name }) }}
          </DBtn>
          <USeparator v-if="providers.length && showPassword" :label="$t('基盤管理者')" />
          <form v-if="showPassword" class="grid gap-3" @submit.prevent="onSubmit">
            <DFormAlert :errors="errors" />
            <DTextField
              v-model="form.email"
              type="email"
              autocomplete="username"
              :label="$t('メールアドレス')"
              :error-messages="errors.email"
            />
            <DTextField
              v-model="form.password"
              type="password"
              autocomplete="current-password"
              :label="$t('パスワード')"
              :error-messages="errors.password"
            />
            <DBtn type="submit" block size="lg" :loading="loading" :disabled="!form.email || !form.password">
              {{ $t('ログイン') }}
            </DBtn>
          </form>
          <UAlert
            v-if="!providers.length && !showPassword"
            color="warning"
            variant="subtle"
            :title="$t('ログインの方法が設定されていません。基盤管理者に連絡してください。')"
          />
        </template>
      </div>
    </UCard>
  </div>
</template>
