<script setup lang="ts">
import { AxiosError } from 'axios'
import RuntimeProfileForm from '~/components/runtime-profile/RuntimeProfileForm.vue'

const emit = defineEmits<{ changed: [] }>()
const route = useRoute()
const router = useRouter()
const api = useAPI()
const toast = useToast()
const confirm = useConfirmDialog()
const { t } = useI18n()
const { hasPermission } = usePermission()
const runtimeProfileId = route.params.runtimeProfileId as string

const {
  data: profile,
  error,
  refresh,
} = await useAsyncData(`runtime-profile-${runtimeProfileId}`, () => api.manage.runtimeProfiles(runtimeProfileId).get())
if (error.value) throw createError(error.value) // 404 / 403 はエラー画面にする

const formRef = ref<InstanceType<typeof RuntimeProfileForm> | null>(null)
const tab = ref('settings')
const versionsReloadKey = ref(0)
// 編集の間に他の人が版を作っていた（409）。最新の状態を読み込んでから編集し直す
const conflicted = ref(false)

const close = () => router.push('/manage/runtime_profiles')

const onUpdated = () => {
  conflicted.value = false
  versionsReloadKey.value = Date.now()
  emit('changed')
}
const onFailed = (err: unknown) => {
  conflicted.value = err instanceof AxiosError && err.response?.status === 409
}
const reloadLatest = async () => {
  await refresh()
  conflicted.value = false
  versionsReloadKey.value = Date.now()
}

const makingDefault = ref(false)
const onMakeDefault = async () => {
  makingDefault.value = true
  try {
    profile.value = await api.manage.runtimeProfiles(runtimeProfileId).makeDefault()
    toast.add({ title: t('既定にしました。これから始まる実行が使います'), color: 'success' })
    emit('changed')
  } catch (err) {
    toast.add({ title: t('エラー'), description: makeErrorMsg(err), color: 'error' })
  } finally {
    makingDefault.value = false
  }
}

const onDelete = async () => {
  const ok = await confirm({
    title: t('Runtime Profileを削除しますか？'),
    description: profile.value?.isDefault
      ? t('既定のRuntime Profileです。削除すると、これから始まる実行はコマンドを使えなくなります')
      : t('版の履歴は残ります'),
    confirmLabel: t('削除'),
    color: 'error',
  })
  if (!ok) return
  try {
    await api.manage.runtimeProfiles(runtimeProfileId).delete()
    toast.add({ title: t('削除しました'), color: 'success' })
    emit('changed')
    close()
  } catch (err) {
    toast.add({ title: t('エラー'), description: makeErrorMsg(err), color: 'error' })
  }
}

const tabs = computed(() => [
  { label: t('設定'), value: 'settings', slot: 'settings' as const },
  { label: t('版の履歴'), value: 'versions', slot: 'versions' as const },
])
</script>

<template>
  <DDrawer
    :open="true"
    :title="$t('Runtime Profile')"
    :description="profile ? $t('現在の版：{number}', { number: profile.version ?? 0 }) : undefined"
    :ui="{ content: 'sm:max-w-xl' }"
    @update:open="(open) => !open && close()"
  >
    <template #content>
      <UTabs v-model="tab" :items="tabs" variant="link" :unmount-on-hide="false">
        <template #settings>
          <div class="grid gap-4 pt-4">
            <UAlert
              v-if="conflicted"
              color="warning"
              variant="subtle"
              icon="i-lucide-refresh-cw"
              :title="$t('編集の間に、他の人が新しい版を作りました')"
              :actions="[
                { label: $t('最新の状態を読み込む'), color: 'warning', variant: 'outline', onClick: reloadLatest },
              ]"
            />
            <UAlert
              v-if="profile?.isDefault"
              color="primary"
              variant="subtle"
              icon="i-lucide-star"
              :title="$t('既定のRuntime Profileです。コマンドを使う実行は、始まった時点のこの版を使います')"
            />
            <RuntimeProfileForm ref="formRef" v-model="profile" @updated="onUpdated" @failed="onFailed" />
          </div>
        </template>
        <template #versions>
          <div class="pt-4">
            <RuntimeProfileVersionList v-model:reload-key="versionsReloadKey" :runtime-profile-id="runtimeProfileId" />
          </div>
        </template>
      </UTabs>
    </template>
    <template #footer>
      <!-- 削除・既定の変更はサーバー側でも権限を確認し、権限がなければ403になる -->
      <template v-if="hasPermission(PermissionCode.CHANGE_RUNTIME_PROFILE)">
        <DBtn color="error" variant="ghost" @click="onDelete">{{ $t('削除') }}</DBtn>
        <DBtn
          v-if="profile && !profile.isDefault"
          color="neutral"
          variant="outline"
          icon="i-lucide-star"
          :disabled="!profile.isActive"
          :loading="makingDefault"
          @click="onMakeDefault"
        >
          {{ $t('既定にする') }}
        </DBtn>
      </template>
      <div class="flex-1" />
      <DBtn
        v-if="tab === 'settings'"
        :disabled="!formRef?.canPost"
        :loading="formRef?.loading"
        @click="formRef?.onSubmit()"
      >
        {{ $t('更新') }}
      </DBtn>
    </template>
  </DDrawer>
</template>
