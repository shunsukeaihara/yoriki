<script setup lang="ts">
// 招待・パスワードの再設定のリンク。token はアクセスログに残らないよう URL のフラグメントにあり、API へは本文で送る
definePageMeta({ layout: 'anonymous', auth: false })

const { t } = useI18n()
const route = useRoute()
const api = useAPI()
const account = useAccountStore()

const token = route.hash.replace(/^#/, '')
const invitation = ref<InvitationToken | null>(null)
const invalidMessage = ref<string | null>(null)

if (!token) {
  invalidMessage.value = t('リンクが正しくありません。受け取ったURLをそのまま開いてください。')
} else {
  try {
    invitation.value = await api.auth.invitations.verify(token)
  } catch (err) {
    // 期限切れ・使用済み、または SSO が有効になって使えなくなったリンク。理由はサーバーの文で表示する
    const errors = getValidationError(err)
    invalidMessage.value = errors.token?.[0] ?? errors.nonFieldErrors?.[0] ?? makeErrorMsg(err)
  }
}

const form = ref({ password: '', passwordConfirm: '' })
const mismatch = computed(
  () => Boolean(form.value.passwordConfirm) && form.value.password !== form.value.passwordConfirm,
)

const { onSubmit, loading, errors } = createSubmit({
  form,
  fn: ({ password }) => api.auth.invitations.accept({ token, password }),
  onSuccess: async () => {
    await account.init()
    await navigateTo('/')
  },
})
</script>

<template>
  <div class="flex min-h-svh items-center justify-center p-4">
    <UCard class="w-full max-w-sm">
      <template #header>
        <h1 class="text-xl font-bold">Yoriki</h1>
        <p v-if="invitation" class="mt-1 text-sm text-muted">
          {{
            invitation.purpose === InvitationPurpose.INVITE
              ? $t('パスワードを設定して、利用を始めます。')
              : $t('新しいパスワードを設定します。')
          }}
        </p>
      </template>

      <div v-if="invalidMessage" class="grid gap-3">
        <UAlert color="error" variant="subtle" :title="invalidMessage" />
        <DBtn to="/login" color="neutral" variant="outline" block>{{ $t('ログイン画面へ') }}</DBtn>
      </div>
      <form v-else-if="invitation" class="grid gap-3" @submit.prevent="onSubmit">
        <DFormAlert :errors="{ nonFieldErrors: [...(errors.nonFieldErrors ?? []), ...(errors.token ?? [])] }" />
        <DTextField :model-value="invitation.email" :label="$t('メールアドレス')" disabled autocomplete="username" />
        <DTextField
          v-model="form.password"
          type="password"
          autocomplete="new-password"
          :label="$t('パスワード')"
          :hint="$t('12文字以上。よく使われるパスワードや、メールアドレスに似たものは使えません')"
          :error-messages="errors.password"
        />
        <DTextField
          v-model="form.passwordConfirm"
          type="password"
          autocomplete="new-password"
          :label="$t('パスワード（確認）')"
          :error-messages="mismatch ? [$t('パスワードが一致しません')] : undefined"
        />
        <p class="text-xs text-muted">
          {{ $t('このリンクの有効期限：{date}', { date: formatDatetime(invitation.expiresAt) }) }}
        </p>
        <DBtn
          type="submit"
          block
          size="lg"
          :loading="loading"
          :disabled="!form.password || form.password !== form.passwordConfirm"
        >
          {{ $t('パスワードを設定してログイン') }}
        </DBtn>
      </form>
    </UCard>
  </div>
</template>
