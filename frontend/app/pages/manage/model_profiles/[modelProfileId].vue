<script setup lang="ts">
import { AxiosError } from 'axios'
import ModelProfileForm from '~/components/model-profile/ModelProfileForm.vue'

const emit = defineEmits<{ changed: [] }>()
const route = useRoute()
const router = useRouter()
const api = useAPI()
const toast = useToast()
const confirm = useConfirmDialog()
const { t } = useI18n()
const { hasPermission } = usePermission()
const modelProfileId = route.params.modelProfileId as string

const {
  data: profile,
  error,
  refresh,
} = await useAsyncData(`model-profile-${modelProfileId}`, () => api.manage.modelProfiles(modelProfileId).get())
if (error.value) throw createError(error.value) // 404 / 403 はエラー画面にする

const formRef = ref<InstanceType<typeof ModelProfileForm> | null>(null)
const tab = ref('settings')
const versionsReloadKey = ref(0)
// 編集の間に他の人が版を作っていた（409）。最新の状態を読み込んでから編集し直す
const conflicted = ref(false)

const close = () => router.push('/manage/model_profiles')

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

const onDelete = async () => {
  const ok = await confirm({
    title: t('モデル設定を削除しますか？'),
    description: t('版の履歴は残ります'),
    confirmLabel: t('削除'),
    color: 'error',
  })
  if (!ok) return
  try {
    await api.manage.modelProfiles(modelProfileId).delete()
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
    :title="$t('モデル設定')"
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
            <ModelProfileForm ref="formRef" v-model="profile" @updated="onUpdated" @failed="onFailed" />
          </div>
        </template>
        <template #versions>
          <div class="pt-4">
            <ModelProfileVersionList v-model:reload-key="versionsReloadKey" :model-profile-id="modelProfileId" />
          </div>
        </template>
      </UTabs>
    </template>
    <template #footer>
      <!-- 削除はサーバー側でも権限を確認し、権限がなければ403になる -->
      <DBtn v-if="hasPermission(PermissionCode.CHANGE_MODEL_PROFILE)" color="error" variant="ghost" @click="onDelete">
        {{ $t('削除') }}
      </DBtn>
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
