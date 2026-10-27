<script setup lang="ts">
import SsoProviderForm from '~/components/sso-provider/SsoProviderForm.vue'

const emit = defineEmits<{ changed: [] }>()
const route = useRoute()
const router = useRouter()
const api = useAPI()
const toast = useToast()
const confirm = useConfirmDialog()
const { t } = useI18n()
const { hasPermission } = usePermission()
const ssoProviderId = route.params.ssoProviderId as string

const { data: provider, error } = await useAsyncData(`sso-provider-${ssoProviderId}`, () =>
  api.manage.ssoProviders(ssoProviderId).get(),
)
if (error.value) throw createError(error.value) // 404 / 403 はエラー画面にする

const formRef = ref<InstanceType<typeof SsoProviderForm> | null>(null)
const close = () => router.push('/manage/sso_providers')

const onDelete = async () => {
  const ok = await confirm({
    title: t('SSOの設定を削除しますか？'),
    description: t('この認証基盤ではログインできなくなります。ログイン中の社員のセッションは切れません'),
    confirmLabel: t('削除'),
    color: 'error',
  })
  if (!ok) return
  try {
    await api.manage.ssoProviders(ssoProviderId).delete()
    toast.add({ title: t('削除しました'), color: 'success' })
    emit('changed')
    close()
  } catch (err) {
    toast.add({ title: t('エラー'), description: makeErrorMsg(err), color: 'error' })
  }
}
</script>

<template>
  <DDrawer :open="true" :title="$t('SSOの設定')" @update:open="(open) => !open && close()">
    <template #content>
      <SsoProviderForm ref="formRef" v-model="provider" @updated="emit('changed')" />
    </template>
    <template #footer>
      <!-- 削除はサーバー側でも権限を確認し、権限がなければ403になる -->
      <DBtn v-if="hasPermission(PermissionCode.DELETE_SSO_PROVIDER)" color="error" variant="ghost" @click="onDelete">
        {{ $t('削除') }}
      </DBtn>
      <div class="flex-1" />
      <DBtn
        v-if="hasPermission(PermissionCode.CHANGE_SSO_PROVIDER)"
        :disabled="!formRef?.canPost"
        :loading="formRef?.loading"
        @click="formRef?.onSubmit()"
      >
        {{ $t('更新') }}
      </DBtn>
    </template>
  </DDrawer>
</template>
