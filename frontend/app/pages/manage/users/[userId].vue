<script setup lang="ts">
import ManageUserForm from '~/components/manage-user/ManageUserForm.vue'

const emit = defineEmits<{ changed: [] }>()
const route = useRoute()
const router = useRouter()
const api = useAPI()
const toast = useToast()
const { t } = useI18n()
const userId = route.params.userId as string

const { data: user, error } = await useAsyncData(`manage-user-${userId}`, () => api.manage.users(userId).get())
if (error.value) throw createError(error.value) // 404 / 403 はエラー画面にする

const formRef = ref<InstanceType<typeof ManageUserForm> | null>(null)
const close = () => router.push('/manage/users')

// 変更・リンクの発行の可否はサーバーが判定する。自分の持たない権限を持つアカウントは変えられず、
// SSO が有効な間は基盤管理者以外にリンクを発行できない（authorization.md §10）
const canChange = computed(() => Boolean(user.value?.canChange))
const canIssue = computed(() => Boolean(user.value?.canIssueInvitation))
const issueDescription = computed(() => {
  if (canIssue.value) return t('リンクを発行し、本人に渡します。前に発行した未使用のリンクは使えなくなります')
  if (!canChange.value) return t('自分の持たない権限を持つアカウントには、リンクを発行できません')
  return t('SSOが有効な間は、SSOでログインします。リンクは基盤管理者のアカウントにだけ発行できます')
})
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
        <UAlert
          v-if="!canChange"
          color="warning"
          variant="subtle"
          icon="i-lucide-lock"
          :title="$t('このアカウントは変更できません')"
          :description="$t('自分の持たない権限を持つアカウント（基盤管理者を含む）は、変更できません')"
        />
        <ManageUserForm ref="formRef" v-model="user" :disabled="!canChange" @updated="emit('changed')" />
        <USeparator />
        <DFormSection
          :title="user?.hasPassword ? $t('パスワードの再設定') : $t('招待')"
          :description="issueDescription"
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
      <DBtn v-if="canChange" :disabled="!formRef?.canPost" :loading="formRef?.loading" @click="formRef?.onSubmit()">
        {{ $t('更新') }}
      </DBtn>
    </template>
  </DDrawer>
</template>
