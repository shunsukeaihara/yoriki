<script setup lang="ts">
import ManageUserForm from '~/components/manage-user/ManageUserForm.vue'

const emit = defineEmits<{ changed: [] }>()
const route = useRoute()
const router = useRouter()
const api = useAPI()
const toast = useToast()
const { t } = useI18n()
const userId = route.params.userId as string

const [{ data: user, error }, { data: methods }] = await Promise.all([
  useAsyncData(`manage-user-${userId}`, () => api.manage.users(userId).get()),
  useAsyncData('auth-methods', () => api.auth.methods.get()),
])
if (error.value) throw createError(error.value) // 404 / 403 はエラー画面にする

const formRef = ref<InstanceType<typeof ManageUserForm> | null>(null)
const close = () => router.push('/manage/users')

// SSO が有効な間は、基盤管理者以外にはリンクを発行できない（サーバーでも断る）
const canIssue = computed(() => Boolean(methods.value?.passwordLogin) || Boolean(user.value?.isPlatformAdmin))
const link = ref<InvitationLink | null>(null)
const issuing = ref(false)
const onIssue = async () => {
  issuing.value = true
  try {
    link.value = await api.manage.users(userId).invitation()
  } catch (err) {
    toast.add({ title: t('エラー'), description: makeErrorMsg(err), color: 'error' })
  } finally {
    issuing.value = false
  }
}
</script>

<template>
  <DDrawer :open="true" :title="$t('ユーザー')" @update:open="(open) => !open && close()">
    <template #content>
      <div class="grid gap-6">
        <ManageUserForm ref="formRef" v-model="user" @updated="emit('changed')" />
        <USeparator />
        <DFormSection
          :title="user?.hasPassword ? $t('パスワードの再設定') : $t('招待')"
          :description="
            canIssue
              ? $t('リンクを発行し、本人に渡します。前に発行した未使用のリンクは使えなくなります')
              : $t('SSOが有効な間は、SSOでログインします。リンクは基盤管理者のアカウントにだけ発行できます')
          "
        >
          <div>
            <DBtn
              color="neutral"
              variant="outline"
              icon="i-lucide-link"
              :disabled="!canIssue"
              :loading="issuing"
              @click="onIssue"
            >
              {{ $t('リンクを発行') }}
            </DBtn>
          </div>
          <ManageUserInvitationLink v-if="link" :link="link" />
        </DFormSection>
      </div>
    </template>
    <template #footer>
      <div class="flex-1" />
      <DBtn :disabled="!formRef?.canPost" :loading="formRef?.loading" @click="formRef?.onSubmit()">
        {{ $t('更新') }}
      </DBtn>
    </template>
  </DDrawer>
</template>
